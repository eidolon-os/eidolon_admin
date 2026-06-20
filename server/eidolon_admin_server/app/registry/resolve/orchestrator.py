"""Resolve orchestrator — joins five entities into one runtime envelope.

Sources consulted on each call:

  - admin's DeviceBindingRepository (KV) — device → agent_id
  - admin's AgentMetadataRepository (KV) — agent → (user, template, ...)
  - admin's UserOrchestrator → memory's GET /api/admin/users/{id}
    → memory MCP URL
  - admin's TemplateOrchestrator → agent's GET /api/admin/personas/templates/{id}/raw
    → soul preview text

No own storage — purely an aggregator. Stateless apart from the
orchestrator references it composes.
"""
from __future__ import annotations

import logging

from eidolon_sdk.http import ServiceUnavailable, ServiceUpstreamError

from .._shared import unwrap_detail
from ..agents.repository import AgentMetadataRepository
from ..devices.repository import (
    DeviceBindingRepository,
    HubDeviceClient,
)
from ..schemas.resolve import ResolvedContext, VoiceprintResolveSummary
from ..templates.orchestrator import TemplateOrchestrator
from ..users.orchestrator import UserError, UserNotFound, UserOrchestrator
from ..voiceprints.repository import VoiceprintStore

logger = logging.getLogger(__name__)


# ---- exceptions ------------------------------------------------------------


class ResolveError(Exception):
    """Base — router maps via ``status_code``."""

    status_code: int = 500


class ResolveDeviceNotBound(ResolveError):
    """Device exists in hub but has no binding row in admin's KV.
    Returns 412 — caller must POST to /api/devices/{id}/bind first."""

    status_code = 412


class ResolveDeviceUnavailable(ResolveError):
    """Device exists but is disabled or not approved."""

    status_code = 412


class ResolveUserNoActiveAgent(ResolveError):
    """User exists in memory but admin's metadata has no
    active_agent_id set. Returns 412 — operator must
    POST /api/users/{id}/set-active-agent first."""

    status_code = 412


class ResolveUserUnavailable(ResolveError):
    """User is disabled; runtime callers must not route to it."""

    status_code = 412


class ResolveError404(ResolveError):
    """Device or user doesn't exist."""

    status_code = 404


class ResolveUpstreamDown(ResolveError):
    """Memory, hub, or agent unreachable while resolving."""

    status_code = 503


# ---- orchestrator ----------------------------------------------------------


# Length of the soul preview baked into the context. Just enough for
# log tagging / admin UI hint; the LLM gets the full soul via agent
# project's render endpoint on the hot path.
_SOUL_PREVIEW_CHARS = 200


class ResolveOrchestrator:
    def __init__(
        self,
        *,
        binding_repo: DeviceBindingRepository,
        hub_client: HubDeviceClient | None = None,
        agent_meta_repo: AgentMetadataRepository,
        user_orchestrator: UserOrchestrator,
        template_orchestrator: TemplateOrchestrator,
        voiceprint_store: VoiceprintStore | None = None,
    ) -> None:
        self._bindings = binding_repo
        self._hub = hub_client
        self._agents = agent_meta_repo
        self._users = user_orchestrator
        self._templates = template_orchestrator
        self._voiceprints = voiceprint_store

    # ---- private helpers ----------------------------------------------

    async def _compose_context(
        self,
        *,
        agent_id: str,
        device_id: str | None,
        interaction_mode: str | None = None,
    ) -> ResolvedContext:
        """Given an agent_id (and optionally a device_id), assemble the
        runtime context. Raises ResolveError404/ResolveUpstreamDown as
        the underlying lookups fail."""

        # Agent metadata: tenant, user, template
        agent_meta = await self._agents.get(agent_id)
        if agent_meta is None:
            raise ResolveError404(
                f"agent {agent_id!r} not found in admin registry "
                "(device/user may reference a deleted agent)"
            )

        # User → memory MCP URL.
        try:
            user_view = await self._users.get_user(agent_meta.user_id)
        except UserNotFound as exc:
            raise ResolveError404(
                f"agent {agent_id!r} references user "
                f"{agent_meta.user_id!r} which doesn't exist in memory"
            ) from exc
        except UserError as exc:
            # Phase 29.H: narrowed from bare ``Exception`` — only catch
            # admin's own UserError subclasses (UserMemoryDown etc.) and
            # let programming bugs surface. UserMemoryDown is the
            # expected 503 path.
            raise ResolveUpstreamDown(
                f"memory unreachable resolving user "
                f"{agent_meta.user_id!r}: {exc}"
            ) from exc

        # Memory MCP url: memory is authoritative for the per-user port
        # assignment. 29.K extended memory's user view envelope with
        # ``mcp_http_url`` so we can read it directly from the user we
        # just fetched — no second round-trip, no synthesis. If memory
        # returned an empty string (very early in user_admin lifecycle
        # or some future degraded mode), we propagate it as-is; channel
        # will see the empty string and refuse to dial.
        if not user_view.spec.enabled:
            raise ResolveUserUnavailable(
                f"user {agent_meta.user_id!r} is disabled"
            )
        if (
            not user_view.health.worker_running
            or not user_view.health.mcp_reachable
        ):
            raise ResolveUpstreamDown(
                f"memory worker for user {agent_meta.user_id!r} is not reachable"
            )
        memory_mcp_url = user_view.mcp_http_url

        # Soul preview — small, only for log tagging
        try:
            raw_yaml = await self._templates._client.get_template_raw(
                agent_meta.template_id
            )
            soul_preview = raw_yaml[:_SOUL_PREVIEW_CHARS]
        except Exception as exc:
            # Soul preview is non-fatal — log + leave empty so resolve
            # still works in the rare case agent project's template
            # endpoint blips.
            logger.warning(
                "resolve: soul preview lookup failed for template %s: %s",
                agent_meta.template_id, exc,
            )
            soul_preview = ""

        voiceprint = VoiceprintResolveSummary()
        if self._voiceprints is not None:
            profile = self._voiceprints.get_profile(
                tenant_id=agent_meta.tenant_id,
                user_id=agent_meta.user_id,
            )
            if profile is not None:
                voiceprint = VoiceprintResolveSummary(
                    enabled=True,
                    profile_id=profile.profile_id,
                    provider=profile.provider,
                    model=profile.model,
                    threshold=profile.threshold,
                )

        return ResolvedContext(
            tenant_id=agent_meta.tenant_id,
            user_id=agent_meta.user_id,
            agent_id=agent_id,
            template_id=agent_meta.template_id,
            template_revision=agent_meta.template_revision,
            agent_runtime_url="",  # not yet exposed
            memory_mcp_url=memory_mcp_url,
            soul_preview=soul_preview,
            device_id=device_id,
            interaction_mode=interaction_mode,
            voiceprint=voiceprint,
        )

    # ---- public API ----------------------------------------------------

    async def _assert_device_available(self, device_id: str) -> None:
        if self._hub is None:
            return
        try:
            record = await self._hub.get_device(device_id)
        except ServiceUnavailable as exc:
            raise ResolveUpstreamDown(
                f"hub unreachable resolving device {device_id!r}: {exc}"
            ) from exc
        except ServiceUpstreamError as exc:
            detail = unwrap_detail(exc.message)
            if exc.status_code == 404:
                raise ResolveError404(detail) from exc
            raise ResolveUpstreamDown(
                f"hub returned {exc.status_code} resolving device "
                f"{device_id!r}: {detail}"
            ) from exc
        if not record.get("enabled", True):
            raise ResolveDeviceUnavailable(f"device {device_id!r} is disabled")
        if not record.get("approved", False):
            raise ResolveDeviceUnavailable(
                f"device {device_id!r} is not approved"
            )

    async def resolve_device(self, device_id: str) -> ResolvedContext:
        """Resolve a device through to its full runtime context.

        Failure modes:
          - binding missing → 412 "device not configured"
          - binding present but agent gone (drift) → 404 with diagnostic
          - underlying lookups fail (memory/agent down) → 503
        """
        binding = await self._bindings.get(device_id)
        if binding is None:
            raise ResolveDeviceNotBound(
                f"device {device_id!r} is not bound to an agent. "
                "POST /api/devices/{id}/bind to configure."
            )
        await self._assert_device_available(device_id)
        return await self._compose_context(
            agent_id=binding.agent_id,
            device_id=device_id,
            interaction_mode=binding.interaction_mode,
        )

    async def resolve_user(self, user_id: str) -> ResolvedContext:
        """Resolve a user through their active_agent_id.

        Failure modes:
          - user has no admin metadata → 404
          - admin metadata has no active_agent_id → 412
          - underlying lookups fail → 503
        """
        # We use list_all to avoid a second roundtrip for active_agent_id
        # — get_user already calls memory; using the metadata repo
        # directly here is one KV read.
        meta = await self._users._meta.get(user_id)  # type: ignore[attr-defined]
        if meta is None:
            raise ResolveError404(
                f"user {user_id!r} not registered with admin "
                "(does memory know about them but admin doesn't? — "
                "create via POST /api/users to claim)"
            )
        if not meta.active_agent_id:
            raise ResolveUserNoActiveAgent(
                f"user {user_id!r} has no active agent. POST "
                f"/api/users/{user_id}/set-active-agent to configure."
            )
        return await self._compose_context(
            agent_id=meta.active_agent_id, device_id=None,
        )


# NOTE: an earlier 29.G placeholder ``_build_memory_mcp_url`` synthesized
# the URL from a port range convention. 29.K removed it — memory now
# exposes the URL on its user view envelope and resolve reads it
# directly. See ``_compose_context`` above.
