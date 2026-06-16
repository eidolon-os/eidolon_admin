"""Templates orchestration — translate agent's wire format to admin's schema.

Two-way translation:

  agent → admin
    agent returns ``PersonaTemplateSummary`` (template_id, revision, name,
    archetype, description) for list, and a richer parsed object for get.
    Admin's ``TemplateRef`` / ``TemplateDetail`` are sliced from those.

  admin → agent
    admin's ``CreateTemplateRequest`` carries a fairly thin payload that
    maps 1:1 to agent's create body. ``ForkTemplateRequest`` similarly.

What this layer adds on top of the raw HTTP proxy:

  - **status-code translation**: agent's 404/409/422 → admin's
    ``TemplateNotFound`` / ``TemplateError(409)`` / ``TemplateError(422)``
    with names the admin router can recognize. Unreachable agent
    becomes a single 503.

  - **identity of source**: agent doesn't tell admin "this is a builtin
    vs custom" in its summary — admin needs to know so the UI can hide
    edit/delete buttons for builtin rows. We compute source from the
    template_id intersection between the list response and an explicit
    "show me only customs" call (or, simpler: agent's get_template_raw
    exposes the source indirectly). For now we surface source="custom"
    when refcount/edit info is available, otherwise "builtin". Pragmatic
    but improvable.

  - **agent_refcount on detail**: TemplateDetail needs this. Agent doesn't
    return it from the standard get; admin orchestrator could call a
    secondary endpoint or punt to "the operator has to attempt DELETE to
    learn the count". For Phase 29.D we punt — refcount surfaces as
    part of the 409 message on DELETE attempt. The schema field stays
    at -1 (unknown) until we add a dedicated agent endpoint.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from eidolon_sdk.http import ServiceUnavailable, ServiceUpstreamError

from .._shared import unwrap_detail
from ..schemas.template import (
    CreateTemplateRequest,
    ForkTemplateRequest,
    TemplateDetail,
    TemplateRef,
    UpdateTemplateRequest,
)
from .repository import TemplateAgentClient

logger = logging.getLogger(__name__)


# ---- exceptions ------------------------------------------------------------


class TemplateError(Exception):
    """Router maps subclasses via ``status_code``."""

    status_code: int = 500


class TemplateNotFound(TemplateError):
    status_code = 404


class TemplateConflict(TemplateError):
    status_code = 409


class TemplateInvalid(TemplateError):
    """Validation failure surfaced from agent (e.g. bad YAML)."""

    status_code = 422


class TemplateAgentDown(TemplateError):
    status_code = 503


# ---- orchestrator ----------------------------------------------------------


class TemplateOrchestrator:
    """Translate admin's REST contract to agent's REST contract.

    Stateless apart from the captured ``TemplateAgentClient``. All
    business rules (refcount, builtin-immutability) live in agent;
    admin just maps + propagates.
    """

    def __init__(self, agent_client: TemplateAgentClient) -> None:
        self._client = agent_client

    # -------- helpers -----------------------------------------------------

    def _raise_mapped(self, exc: ServiceUpstreamError) -> None:
        """Map an upstream HTTP status to admin's exception hierarchy.

        Uses the shared :func:`unwrap_detail` to strip FastAPI's
        ``{"detail": "..."}`` envelope so admin's eventual
        ``HTTPException(detail=...)`` doesn't double-wrap.
        """
        message = unwrap_detail(exc.message)
        if exc.status_code == 404:
            raise TemplateNotFound(message)
        if exc.status_code == 409:
            raise TemplateConflict(message)
        if exc.status_code == 422:
            raise TemplateInvalid(message)
        # Anything else is a "broken upstream" — keep the original 5xx
        # spirit but route through TemplateError so the router emits the
        # right code.
        raise TemplateError(f"agent returned {exc.status_code}: {message}")

    @staticmethod
    def _summary_to_ref(
        summary: dict[str, Any], *, source: str, tenant_id: str = "default"
    ) -> TemplateRef:
        """Build admin's TemplateRef from agent's PersonaTemplateSummary dict.

        Agent's summary doesn't carry an updated_at field — we stamp
        current time as a placeholder so the schema validates. Operators
        rely on revision for "which version am I looking at", not
        updated_at.
        """
        return TemplateRef(
            template_id=summary["template_id"],
            tenant_id=tenant_id,
            source=source,  # type: ignore[arg-type]
            revision=summary.get("template_revision", 1),
            display_name=summary.get("name", summary["template_id"]),
            archetype=summary.get("archetype", "unknown"),
            # No updated_at from agent summary — placeholder.
            updated_at=datetime.now(timezone.utc),
        )

    # -------- public API --------------------------------------------------

    async def list_all(self) -> list[TemplateRef]:
        """Return all templates (builtin + custom) as TemplateRef.

        Agent's list returns summaries without source/tenant info. We
        synthesize source by intersecting: for each summary, if a
        ``/raw-custom`` GET succeeds it's custom, otherwise builtin.
        That's N+1 calls — fine for the small template counts we expect
        (typically <20). If this becomes a bottleneck we add a
        dedicated agent endpoint that returns source inline.

        For Phase 29.D we use a simpler heuristic: agent stamps custom
        templates with revision > 1 after any edit, but builtin defaults
        to revision = 1 too — so revision can't distinguish. Use the
        agent's ``/list-by-source`` if exposed, else default to "custom"
        for any id NOT in the builtin set (we'd need to know that set).

        Pragmatic approach for this phase: ask agent for the list, mark
        all as ``"custom"`` provisionally. The UI presents both kinds
        uniformly; a follow-up phase adds the source distinguisher.
        """
        try:
            raw_list = await self._client.list_templates()
        except ServiceUnavailable as exc:
            raise TemplateAgentDown(str(exc)) from exc
        except ServiceUpstreamError as exc:
            self._raise_mapped(exc)

        return [
            self._summary_to_ref(s, source="custom")  # see docstring
            for s in raw_list
        ]

    async def get(self, template_id: str) -> TemplateDetail:
        """Full detail. Combines agent's parsed object + raw yaml."""
        try:
            parsed = await self._client.get_template(template_id)
            raw_yaml = await self._client.get_template_raw(template_id)
        except ServiceUnavailable as exc:
            raise TemplateAgentDown(str(exc)) from exc
        except ServiceUpstreamError as exc:
            self._raise_mapped(exc)

        meta = parsed.get("metadata", {})
        ref = TemplateRef(
            template_id=meta.get("template_id", template_id),
            tenant_id="default",
            source="custom",  # see list_all docstring re: source detection
            revision=meta.get("template_revision", 1),
            display_name=meta.get("name", template_id),
            archetype=meta.get("archetype", "unknown"),
            updated_at=datetime.now(timezone.utc),
        )
        return TemplateDetail(
            ref=ref,
            yaml_body=raw_yaml,
            # Refcount is not exposed by agent's standard get. Sentinel
            # value 0 means "unknown" here — operator learns true count
            # on DELETE attempt (agent's 409 message includes it).
            agent_refcount=0,
        )

    async def create(self, body: CreateTemplateRequest) -> TemplateRef:
        try:
            row = await self._client.create_custom(
                template_id=body.template_id,
                tenant_id=body.tenant_id,
                display_name=body.display_name,
                yaml_body=body.yaml_body,
            )
        except ServiceUnavailable as exc:
            raise TemplateAgentDown(str(exc)) from exc
        except ServiceUpstreamError as exc:
            self._raise_mapped(exc)

        return TemplateRef(
            template_id=row["template_id"],
            tenant_id=row["tenant_id"],
            source="custom",
            revision=row["revision"],
            display_name=row["display_name"],
            archetype=row.get("archetype", "custom"),
            updated_at=_parse_dt(row["updated_at"]),
        )

    async def update(
        self, template_id: str, body: UpdateTemplateRequest
    ) -> TemplateRef:
        try:
            row = await self._client.update_custom(
                template_id,
                display_name=body.display_name,
                yaml_body=body.yaml_body,
            )
        except ServiceUnavailable as exc:
            raise TemplateAgentDown(str(exc)) from exc
        except ServiceUpstreamError as exc:
            self._raise_mapped(exc)

        return TemplateRef(
            template_id=row["template_id"],
            tenant_id=row["tenant_id"],
            source="custom",
            revision=row["revision"],
            display_name=row["display_name"],
            archetype=row.get("archetype", "custom"),
            updated_at=_parse_dt(row["updated_at"]),
        )

    async def delete(self, template_id: str) -> None:
        try:
            await self._client.delete_custom(template_id)
        except ServiceUnavailable as exc:
            raise TemplateAgentDown(str(exc)) from exc
        except ServiceUpstreamError as exc:
            self._raise_mapped(exc)

    async def fork(
        self, template_id: str, body: ForkTemplateRequest
    ) -> TemplateRef:
        try:
            row = await self._client.fork(
                template_id,
                new_template_id=body.new_template_id,
                target_tenant_id=body.target_tenant_id,
                new_display_name=body.new_display_name,
            )
        except ServiceUnavailable as exc:
            raise TemplateAgentDown(str(exc)) from exc
        except ServiceUpstreamError as exc:
            self._raise_mapped(exc)

        return TemplateRef(
            template_id=row["template_id"],
            tenant_id=row["tenant_id"],
            source="custom",
            revision=row["revision"],
            display_name=row["display_name"],
            archetype=row.get("archetype", "custom"),
            updated_at=_parse_dt(row["updated_at"]),
        )


def _parse_dt(s: str) -> datetime:
    # Agent serializes datetime with isoformat(); accept naive (treat
    # as UTC) or aware.
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt
