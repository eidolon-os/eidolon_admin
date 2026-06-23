"""Agents orchestration — cross-project create + cascade delete.

Most complex orchestrator in Phase 29 because every operation touches
THREE concerns:

  - agent project (the persona instance + soul rendering)
  - memory project (verify the owning user exists)
  - admin's own KV (route flat agent_id → composite key + display name)

Create
------
1. Validate owning user exists (via UserOrchestrator)
2. Validate template exists (via TemplateOrchestrator)
3. Generate a fresh agent_id (uuid4)
4. Call agent project to create the persona instance + render soul
5. Write admin metadata KV entry (the routing record)
6. If step 5 fails, roll back step 4 (delete the persona we just made)
7. Optionally mark this agent as the user's active_agent (default behavior)

Delete
------
1. Resolve agent_id → composite key via admin metadata
2. **Cascade**: clear any user's ``active_agent_id`` referring to this
   agent (UserOrchestrator.clear_active_agent_references)
3. Call agent project to delete the persona instance
4. Delete admin metadata KV entry

Boundary
--------
admin NEVER touches agent's SQL or persona files directly — all
mutations go through the agent project's HTTP surface
(``/api/admin/personas/instances*``).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from eidolon_sdk.http import ServiceUnavailable, ServiceUpstreamError

from .._shared import unwrap_detail
from ..schemas.agent import (
    AgentDetail,
    AgentRef,
    CreateAgentRequest,
    KnobOverlay,
)
from ..tenants.orchestrator import TenantNotFound
from ..users.orchestrator import UserError, UserMemoryDown, UserNotFound, UserOrchestrator
from .repository import (
    AgentMetadata,
    AgentMetadataRepository,
    AgentProjectClient,
)

logger = logging.getLogger(__name__)


# ---- exceptions ------------------------------------------------------------


class AgentError(Exception):
    status_code: int = 500


class AgentNotFound(AgentError):
    status_code = 404


class AgentBadRequest(AgentError):
    """Caller's POST/PUT body references a non-existent resource (user
    or template). 400 because the body is the problem, not the endpoint."""

    status_code = 400


class AgentProjectDown(AgentError):
    status_code = 503


class AgentUserMismatch(AgentError):
    """When admin's metadata says agent X belongs to user Y but the
    request claims a different user. Indicates KV/agent drift."""

    status_code = 500


# ---- helper to template-orchestrator dependency ----------------------------


# We don't import TemplateOrchestrator directly (would create a
# subscription cycle if 29.F is built before 29.D — though it isn't,
# the principle stands). Instead the orchestrator takes a "template
# existence checker" callable. main.py wires it from the actual
# TemplateOrchestrator.
TemplateExistsCheck = Callable[[str], Awaitable[bool]]


# ---- orchestrator ----------------------------------------------------------


class AgentOrchestrator:
    def __init__(
        self,
        *,
        agent_client: AgentProjectClient,
        metadata_repo: AgentMetadataRepository,
        user_orchestrator: UserOrchestrator,
        template_exists_check: TemplateExistsCheck,
    ) -> None:
        self._agent = agent_client
        self._meta = metadata_repo
        self._users = user_orchestrator
        self._template_check = template_exists_check
        # Devices module installs this so agent-delete unbinds devices.
        # None when Devices isn't wired yet (e.g. tests of agents in
        # isolation, or admin booted without hub).
        self._device_cascade_hook: Callable[[str], Awaitable[list[str]]] | None = None

    def set_device_cascade_hook(
        self,
        hook: Callable[[str], Awaitable[list[str]]] | None,
    ) -> None:
        """Wire the device-unbind cascade. Called by lifespan after both
        agent + device orchestrators exist. Setter (not constructor) to
        avoid circular dependency at module-construction time."""
        self._device_cascade_hook = hook

    # ---- helpers -------------------------------------------------------

    def _map_agent_error(self, exc: ServiceUpstreamError) -> None:
        message = unwrap_detail(exc.message)
        if exc.status_code == 404:
            raise AgentNotFound(message)
        raise AgentError(f"agent returned {exc.status_code}: {message}")

    @staticmethod
    def _new_agent_id() -> str:
        # 12-char hex prefix is plenty for our scale (~10^14 keys → 1%
        # collision at ~10^7 agents, won't happen in single-deploy life)
        return f"ag_{uuid.uuid4().hex[:12]}"

    @staticmethod
    def _make_default_display_name(user_id: str, template_id: str) -> str:
        return f"{user_id}'s {template_id}"

    @staticmethod
    def _build_ref(agent_id: str, meta: AgentMetadata, *, is_active: bool) -> AgentRef:
        # The schemas need datetimes, KV stores ISO strings.
        created = (
            datetime.fromisoformat(meta.created_at)
            if meta.created_at
            else datetime.now(timezone.utc)
        )
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        return AgentRef(
            agent_id=agent_id,
            user_id=meta.user_id,
            template_id=meta.template_id,
            template_revision=meta.template_revision,
            display_name=meta.display_name or agent_id,
            created_at=created,
            updated_at=created,  # KV doesn't track updated separately yet
            is_active_for_user=is_active,
        )

    async def _user_active_agent(self, user_id: str) -> str | None:
        """Return the user's active_agent_id, or None when unknown.

        Phase 29.H: previously this swallowed every exception silently.
        Now we differentiate three cases:

          - UserNotFound      → None (user genuinely doesn't exist)
          - UserMemoryDown    → None + log (transient infra; the list
                                 view should still render — UI shows
                                 "is_active=False" for the row, which
                                 is honest because we can't confirm)
          - any other         → propagate (real bug surfaces loudly)

        The narrower handling avoids the silent-fallback anti-pattern
        while keeping the LIST view usable under partial-degradation
        conditions (operator wants to see what agents exist even if
        memory blips).
        """
        try:
            view = await self._users.get_user(user_id)
        except UserNotFound:
            return None
        except UserMemoryDown as exc:
            logger.warning(
                "agent list: memory unreachable resolving active_agent for "
                "user=%s (%s); rendering with is_active=False",
                user_id, exc,
            )
            return None
        return view.active_agent_id

    # ---- list / get ----------------------------------------------------

    async def list_agent_ids_for_user(self, user_id: str) -> list[str]:
        """KV-only lookup of agent_ids by user. No upstream HTTP calls —
        used by UserOrchestrator to decorate UserView.agent_ids without
        the cost of the active-flag computation list_agents does.

        Stable order (KV-list order is insertion-ish; we sort to keep the
        UI dropdown deterministic across refreshes).
        """
        pairs = await self._meta.list_by_user(user_id)
        return sorted(aid for aid, _ in pairs)

    async def list_agents(self, *, user_id: str | None = None) -> list[AgentRef]:
        """List all agents (or filter by user_id). Active flag is computed
        per-row by consulting the corresponding user's active_agent_id."""
        if user_id is not None:
            pairs = await self._meta.list_by_user(user_id)
        else:
            all_meta = await self._meta.list_all()
            pairs = list(all_meta.items())

        # Compute active flags. We could cache the per-user lookup but
        # the list is small in practice and the extra correctness wins.
        active_by_user: dict[str, str | None] = {}

        async def active_for(uid: str) -> str | None:
            if uid not in active_by_user:
                active_by_user[uid] = await self._user_active_agent(uid)
            return active_by_user[uid]

        out: list[AgentRef] = []
        for agent_id, meta in pairs:
            is_active = (await active_for(meta.user_id)) == agent_id
            out.append(self._build_ref(agent_id, meta, is_active=is_active))
        # Stable order: by created_at (oldest first)
        return sorted(out, key=lambda a: a.created_at)

    async def get_agent(self, agent_id: str) -> AgentDetail:
        meta = await self._meta.get(agent_id)
        if meta is None:
            raise AgentNotFound(f"agent {agent_id!r} not found in admin registry")
        # Pull the live persona from agent project for soul + knobs
        try:
            persona = await self._agent.get_instance(
                meta.tenant_id, meta.user_id, agent_id
            )
        except ServiceUnavailable as exc:
            raise AgentProjectDown(str(exc)) from exc
        except ServiceUpstreamError as exc:
            self._map_agent_error(exc)

        active = await self._user_active_agent(meta.user_id)
        ref = self._build_ref(agent_id, meta, is_active=(active == agent_id))

        # Render the template against the current persona state to get
        # the soul markdown (29.I fix — 29.F left this empty as a stub).
        # Render is idempotent + side-effect-free per the agent project's
        # contract, so calling it on every get is safe. If render fails
        # (template gone? broken?) we serve an empty soul + log the
        # error so the UI still loads.
        soul_md = ""
        try:
            render_result = await self._agent.render_template(meta.template_id)
            soul_md = render_result.get("markdown", "")
        except (ServiceUnavailable, ServiceUpstreamError) as exc:
            logger.warning(
                "get_agent: soul render failed for template=%s (agent=%s); "
                "returning empty soul. cause=%s",
                meta.template_id, agent_id, exc,
            )

        knobs_raw = persona.get("behavioral_knobs", {})
        # Persona's knob dict has full BehavioralKnob shape; we only
        # need {name: current_value} for the overlay.
        knob_overlays_dict = {
            name: float(k.get("current", 0.0)) for name, k in knobs_raw.items()
            if isinstance(k, dict)
        }
        return AgentDetail(
            ref=ref,
            soul_md=soul_md,
            soul_size_bytes=len(soul_md.encode("utf-8")),
            knob_overlays=KnobOverlay(root=knob_overlays_dict),
            evolution_state=persona.get("evolution_state", {}),
        )

    # ---- create --------------------------------------------------------

    async def create_agent(self, body: CreateAgentRequest) -> AgentRef:
        """Cross-project create with compensation.

        Steps:
          1. validate user exists (memory + admin metadata)
          2. validate template exists (via TemplateExistsCheck)
          3. mint agent_id, call agent project to create persona instance
          4. write admin metadata KV
          5. if step 4 fails: delete persona we just made (rollback)
          6. optionally make this the user's active agent (default True)
        """
        # Step 1 — user must exist
        try:
            user_view = await self._users.get_user(body.user_id)
        except UserNotFound as exc:
            raise AgentBadRequest(
                f"cannot create agent: user {body.user_id!r} not found. "
                "Create the user first via POST /api/users."
            ) from exc

        # Step 2 — template must exist (lookup goes through the checker)
        if not await self._template_check(body.template_id):
            raise AgentBadRequest(
                f"cannot create agent: template {body.template_id!r} "
                "not found. Pick an existing template or fork one."
            )

        # Step 3 — mint id + call agent project
        agent_id = self._new_agent_id()
        tenant_id = user_view.spec.tenant_id
        display_name = (
            body.display_name
            or self._make_default_display_name(body.user_id, body.template_id)
        )
        try:
            persona = await self._agent.create_instance(
                tenant_id=tenant_id,
                user_id=body.user_id,
                instance_id=agent_id,
                template_id=body.template_id,
            )
        except ServiceUnavailable as exc:
            raise AgentProjectDown(str(exc)) from exc
        except ServiceUpstreamError as exc:
            self._map_agent_error(exc)

        # Pull template_revision off the persona's metadata (agent set
        # this at render time).
        template_revision = int(
            persona.get("metadata", {}).get("template_revision", 1)
        )

        meta = AgentMetadata(
            tenant_id=tenant_id,
            user_id=body.user_id,
            template_id=body.template_id,
            template_revision=template_revision,
            display_name=display_name,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        # Step 4 — admin metadata write (with compensation if it fails)
        try:
            await self._meta.put(agent_id, meta)
        except Exception as exc:  # noqa: BLE001 — drives rollback
            logger.exception(
                "agent_create: KV write failed for %s; rolling back "
                "persona instance in agent project",
                agent_id,
            )
            try:
                await self._agent.delete_instance(
                    tenant_id, body.user_id, agent_id
                )
            except Exception:
                logger.exception(
                    "agent_create: rollback (persona delete) also failed "
                    "for %s — manual cleanup needed",
                    agent_id,
                )
            raise AgentError(
                f"failed to persist admin metadata: {exc} "
                "(agent project rolled back)"
            ) from exc

        # Step 5 — set active for user, if requested (default)
        if body.set_active:
            from ..schemas.user import SetActiveAgentRequest
            try:
                await self._users.set_active_agent(
                    body.user_id, SetActiveAgentRequest(agent_id=agent_id)
                )
            except Exception:
                # Non-fatal: agent is created, user just doesn't have
                # it as default yet. Operator can fix via /api/users/.../set-active-agent.
                logger.warning(
                    "agent_create: set_active failed for user=%s agent=%s "
                    "(agent persists; operator can re-set later)",
                    body.user_id, agent_id,
                )

        return self._build_ref(agent_id, meta, is_active=body.set_active)

    # ---- delete --------------------------------------------------------

    async def delete_agent(self, agent_id: str) -> dict[str, Any]:
        """Cascade delete.

        Order:
          1. resolve admin metadata (must exist; else 404)
          2. clear any user.active_agent referring to this agent
             (cascade: agent is going away, so nobody's "active" anymore)
          3. agent project delete (the authoritative persona removal)
          4. delete admin metadata

        If step 3 fails, step 2's cleanup remains — operator can retry
        DELETE which is idempotent.
        """
        meta = await self._meta.get(agent_id)
        if meta is None:
            raise AgentNotFound(f"agent {agent_id!r} not found in admin registry")

        # Step 2 — clear active_agent references (best-effort, never fatal)
        cleared_users: list[str] = []
        try:
            cleared_users = await self._users.clear_active_agent_references(
                agent_id
            )
        except Exception:
            logger.exception(
                "agent_delete: clear_active_agent_references failed for %s; "
                "proceeding anyway",
                agent_id,
            )

        # Step 2b — unbind any devices pointing at this agent (29.G cascade).
        # Best-effort: if Devices isn't wired or fails, we proceed anyway —
        # the device's binding becomes a stale pointer, but admin's resolve
        # endpoint will refuse to use it (returns 404 "agent not found in
        # admin registry") so runtime never gets corrupted state.
        unbound_devices: list[str] = []
        if self._device_cascade_hook is not None:
            try:
                unbound_devices = await self._device_cascade_hook(agent_id)
            except Exception:
                logger.exception(
                    "agent_delete: device cascade hook failed for %s; "
                    "proceeding anyway",
                    agent_id,
                )

        # Step 3 — agent project delete
        try:
            await self._agent.delete_instance(
                meta.tenant_id, meta.user_id, agent_id
            )
        except ServiceUnavailable as exc:
            raise AgentProjectDown(str(exc)) from exc
        except ServiceUpstreamError as exc:
            # If agent returns 404, treat as already-gone (idempotent
            # DELETE) and proceed to clean admin metadata.
            if exc.status_code != 404:
                self._map_agent_error(exc)
            logger.info(
                "agent_delete: agent project says %s already gone — "
                "cleaning admin metadata anyway", agent_id,
            )

        # Step 4 — admin metadata cleanup
        await self._meta.delete(agent_id)

        return {
            "agent_id": agent_id,
            "deleted": True,
            "active_agent_cleared_for_users": cleared_users,
            "unbound_devices": unbound_devices,
        }

    async def revoke_user_sessions(self, user_id: str) -> dict[str, Any]:
        """Invalidate active runtime tokens for ``user_id`` via agent."""
        try:
            return await self._agent.revoke_user_sessions(user_id)
        except ServiceUnavailable as exc:
            raise AgentProjectDown(str(exc)) from exc
        except ServiceUpstreamError as exc:
            self._map_agent_error(exc)

    async def delete_user_data(self, user_id: str) -> dict[str, Any]:
        """Hard-delete agent-owned persistent data for ``user_id``."""
        try:
            return await self._agent.delete_user_data(user_id)
        except ServiceUnavailable as exc:
            raise AgentProjectDown(str(exc)) from exc
        except ServiceUpstreamError as exc:
            self._map_agent_error(exc)

    # ---- evolution history --------------------------------------------

    async def get_evolution_history(
        self, agent_id: str, *, limit: int = 50
    ) -> list[dict[str, Any]]:
        meta = await self._meta.get(agent_id)
        if meta is None:
            raise AgentNotFound(f"agent {agent_id!r} not found")
        try:
            return await self._agent.get_evolution_history(
                meta.tenant_id, meta.user_id, agent_id, limit=limit,
            )
        except ServiceUnavailable as exc:
            raise AgentProjectDown(str(exc)) from exc
        except ServiceUpstreamError as exc:
            self._map_agent_error(exc)
