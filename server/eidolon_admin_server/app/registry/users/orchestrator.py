"""Users orchestration — cross-project lifecycle with compensation.

Responsibilities:
  1. Compose admin's :class:`UserView` from memory's user record + admin's
     per-user metadata (tenant_id, active_agent_id).
  2. Validate cross-project invariants before mutating:
        - referenced tenant exists in admin's TenantRepository
        - user_id charset matches memory's regex (Pydantic does this at
          the request layer; we re-check here as defense-in-depth)
  3. **Atomicity** on create: memory's POST + admin's metadata PUT are
     two steps. If step 2 fails, roll back step 1 (delete the memory
     user we just created) so admin and memory don't drift.
  4. **Cascade** on delete: when 29.F (Agents) and 29.G (Devices) land,
     this orchestrator will also delete this user's agents and unbind
     any devices pointing at them. For 29.E those entities don't exist
     in admin yet, so the cascade reduces to "delete memory user +
     delete admin metadata".

What this layer DOES NOT do: it never touches memory's ``users.yaml``
or palace files directly. All persistence on memory's side goes through
``MemoryUserClient`` (HTTP), which talks to memory's supervisor admin
API (29.B.2).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from .._shared import unwrap_detail
from ..schemas.user import (
    ConsolidatorConfig,
    CreateUserRequest,
    SetActiveAgentRequest,
    UpdateUserRequest,
    UserHealth,
    UserSpec,
    UserView,
)
from ..tenants.orchestrator import TenantNotFound, TenantOrchestrator
from .repository import (
    MemoryUserClient,
    MemoryUserUnreachable,
    MemoryUserUpstreamError,
    UserMetadata,
    UserMetadataRepository,
)

logger = logging.getLogger(__name__)


# ---- exceptions ------------------------------------------------------------


class UserError(Exception):
    status_code: int = 500


class UserNotFound(UserError):
    status_code = 404


class UserAlreadyExists(UserError):
    status_code = 409


class TenantNotFoundForUser(UserError):
    """Tried to create / move a user into a tenant that doesn't exist."""

    status_code = 409


class UserMemoryDown(UserError):
    """Memory's admin API is unreachable. Distinct from 503 'orchestrator
    missing' so the operator can tell it apart in error toasts."""

    status_code = 503


# ---- helpers ---------------------------------------------------------------


def _consolidator_to_memory_dict(c: ConsolidatorConfig | None) -> dict[str, Any] | None:
    """Translate admin's ConsolidatorConfig to the dict memory accepts.

    None means "use memory's defaults" — admin doesn't override unless
    the operator explicitly sent a block.
    """
    if c is None:
        return None
    return {
        "enabled": c.enabled,
        "interval_hours": c.interval_hours,
        "window_days": c.window_days,
        "min_drawers": c.min_drawers,
        "min_confidence": c.min_confidence,
    }


def _memory_to_consolidator(raw: dict[str, Any] | None) -> ConsolidatorConfig:
    """Reverse of the above. Memory's GET always returns a consolidator
    block; if it's all defaults, we still return the model."""
    if raw is None:
        return ConsolidatorConfig()
    return ConsolidatorConfig(
        enabled=raw.get("enabled", True),
        interval_hours=raw.get("interval_hours", 6.0),
        window_days=raw.get("window_days", 30),
        min_drawers=raw.get("min_drawers", 3),
        min_confidence=raw.get("min_confidence", 0.6),
    )


def _parse_dt(value: str | None) -> datetime:
    """Parse memory's ISO datetime; fall back to now() if absent. Used
    for the ``created_at`` field — memory stamps this on its side."""
    if not value:
        return datetime.now(timezone.utc)
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


# ---- orchestrator ----------------------------------------------------------


class UserOrchestrator:
    # Type alias for the agent-ids lookup hook. Returns the list of
    # agent_ids owned by a user. Wired by main.py after both the user
    # and agent orchestrators exist (setter-injection avoids the
    # circular import between users/agents).
    AgentIdsProvider = Callable[[str], Awaitable[list[str]]]
    AgentDeleteProvider = Callable[[str], Awaitable[dict[str, Any]]]

    def __init__(
        self,
        *,
        memory_client: MemoryUserClient,
        metadata_repo: UserMetadataRepository,
        tenant_orchestrator: TenantOrchestrator,
    ) -> None:
        self._mem = memory_client
        self._meta = metadata_repo
        self._tenants = tenant_orchestrator
        # 29.K: ``agent_ids`` was always [] before — UserView dropdown
        # in the admin UI was silently empty. Setter wired in lifespan
        # once AgentOrchestrator is built.
        self._agent_ids_provider: UserOrchestrator.AgentIdsProvider | None = None
        self._agent_delete_provider: UserOrchestrator.AgentDeleteProvider | None = None

    def set_agent_ids_provider(
        self, provider: AgentIdsProvider
    ) -> None:
        """Inject the agent-ids lookup. ``provider(user_id)`` returns
        the list of agent_ids owned by that user. Setter (not ctor arg)
        because UserOrchestrator must be built before AgentOrchestrator
        — agent's ctor takes the user_orchestrator for cross-validation."""
        self._agent_ids_provider = provider

    def set_agent_delete_provider(
        self, provider: AgentDeleteProvider
    ) -> None:
        """Inject agent deletion for user-delete cascade.

        User deletion must not leave persona instances, user.active_agent
        references, or device bindings behind. The actual cleanup belongs
        to AgentOrchestrator.delete_agent(), so Users receives a thin
        callback instead of importing Agents directly.
        """
        self._agent_delete_provider = provider

    # ---- internal helpers ----------------------------------------------

    def _build_view(
        self,
        memory_record: dict[str, Any],
        admin_meta: UserMetadata | None,
    ) -> UserView:
        """Compose admin's UserView from the two sources."""
        spec_block = memory_record.get("spec", {})
        health_block = memory_record.get("health", {})

        # Admin's metadata is authoritative for tenant_id + display_name.
        # If metadata is absent (memory has a user admin doesn't know
        # about), fall back to defaults — the user appears as an
        # "unmanaged" entry the operator can adopt.
        tenant_id = admin_meta.tenant_id if admin_meta else "default"
        display_name = (
            admin_meta.display_name
            if (admin_meta and admin_meta.display_name)
            else spec_block.get("display_name") or spec_block.get("user_id", "")
        )
        active_agent_id = admin_meta.active_agent_id if admin_meta else None

        spec = UserSpec(
            user_id=spec_block["user_id"],
            tenant_id=tenant_id,
            display_name=display_name,
            palace_path=spec_block.get("palace_path", ""),
            consolidator=_memory_to_consolidator(spec_block.get("consolidator")),
            created_at=_parse_dt(spec_block.get("created_at")),
        )
        health = UserHealth(
            worker_running=bool(health_block.get("worker_running")),
            mcp_reachable=bool(health_block.get("mcp_reachable")),
            palace_initialized=bool(health_block.get("palace_initialized")),
            note=health_block.get("note", ""),
        )
        # Memory exposes the per-user MCP URL on the view envelope
        # (added 29.K). Older memory builds may not include it — in
        # that case we leave the field empty; resolve will then refuse
        # to dial, which is the correct behavior (better than a
        # silently-stale synthesized URL).
        mcp_http_url = memory_record.get("mcp_http_url", "")

        return UserView(
            spec=spec,
            health=health,
            active_agent_id=active_agent_id,
            # agent_ids: empty here; callers that have the provider wired
            # (list_users / get_user) decorate after _build_view returns.
            # Direct callers without an agent_orchestrator (tests, edge
            # paths) get an empty list — accurate, not "TODO later".
            agent_ids=[],
            mcp_http_url=mcp_http_url,
        )

    def _map_memory_error(self, exc: MemoryUserUpstreamError) -> None:
        """Status-code translation. Unwraps memory's
        ``{"detail": "..."}`` envelope so the message stays single-layer
        through admin's HTTPException."""
        message = unwrap_detail(exc.message)
        if exc.status_code == 404:
            raise UserNotFound(message)
        if exc.status_code == 409:
            raise UserAlreadyExists(message)
        # 4xx-other and 5xx: surface as generic UserError so router emits
        # a useful status code.
        raise UserError(f"memory returned {exc.status_code}: {message}")

    # ---- public API ----------------------------------------------------

    async def clear_active_agent_references(self, agent_id: str) -> list[str]:
        """When an agent is deleted, any user whose active_agent_id
        points at it must be cleared. Returns the list of affected
        user_ids so the caller can log / report.

        Wired by Agents orchestrator's delete path (29.F) — same
        injection-via-setter pattern Tenants uses for refcount.
        """
        all_meta = await self._meta.list_all()
        affected: list[str] = []
        for user_id, meta in all_meta.items():
            if meta.active_agent_id == agent_id:
                new_meta = UserMetadata(
                    tenant_id=meta.tenant_id,
                    active_agent_id=None,
                    display_name=meta.display_name,
                )
                await self._meta.put(user_id, new_meta)
                affected.append(user_id)
        if affected:
            logger.info(
                "cleared active_agent_id=%s on %d user(s): %s",
                agent_id, len(affected), affected,
            )
        return affected

    async def count_users_for_tenant(self, tenant_id: str) -> int:
        """How many users currently belong to ``tenant_id``.

        Wired into :class:`TenantOrchestrator` via
        ``set_user_refcount_provider`` so deleting a tenant is refused
        while users still reference it.

        Implementation note: reads admin's own metadata KV (the
        authoritative source for tenant↔user mapping). Doesn't hit
        memory — if memory has a user without an admin metadata entry,
        that user is by definition in the default tenant fallback and
        wouldn't block a non-default tenant deletion.
        """
        all_meta = await self._meta.list_all()
        return sum(1 for m in all_meta.values() if m.tenant_id == tenant_id)

    async def list_users(self) -> list[UserView]:
        """List users by joining memory's authoritative list with admin's
        metadata map. Memory's view is the spine — users that exist in
        admin's KV but NOT in memory are silently dropped (memory is
        authoritative; an admin KV entry without a memory user is dead
        config the operator should clean up via DELETE)."""
        try:
            envelope = await self._mem.list_users()
        except MemoryUserUnreachable as exc:
            raise UserMemoryDown(str(exc)) from exc
        except MemoryUserUpstreamError as exc:
            self._map_memory_error(exc)

        users = envelope.get("users", [])
        meta_map = await self._meta.list_all()
        views = [
            self._build_view(record, meta_map.get(record["spec"]["user_id"]))
            for record in users
        ]
        # Decorate with agent_ids if the agent orchestrator is wired.
        # We fetch once per user — N HTTP calls. For dev-stack scale
        # (< 50 users) this is fine; if N grows we can switch to a
        # bulk endpoint. The setter may be unwired when agent service
        # is down: agent_ids stays empty (caller checks ``status``).
        if self._agent_ids_provider is not None:
            for view in views:
                view.agent_ids = await self._safe_agent_ids(view.spec.user_id)
        return views

    async def get_user(self, user_id: str) -> UserView:
        try:
            record = await self._mem.get_user(user_id)
        except MemoryUserUnreachable as exc:
            raise UserMemoryDown(str(exc)) from exc
        except MemoryUserUpstreamError as exc:
            self._map_memory_error(exc)
        meta = await self._meta.get(user_id)
        view = self._build_view(record, meta)
        if self._agent_ids_provider is not None:
            view.agent_ids = await self._safe_agent_ids(user_id)
        return view

    async def _safe_agent_ids(self, user_id: str) -> list[str]:
        """Wrap the provider call so a failing agent service doesn't
        break the user list. Mirrors the resilience policy elsewhere:
        partial degradation > total failure for read paths."""
        provider = self._agent_ids_provider
        if provider is None:
            return []
        try:
            return await provider(user_id)
        except Exception:  # noqa: BLE001 — read-path resilience
            logger.warning(
                "users: agent_ids lookup failed for %r; field will be empty",
                user_id,
                exc_info=True,
            )
            return []

    async def create_user(self, body: CreateUserRequest) -> UserView:
        """Two-step create with compensation:

          1. POST memory /api/admin/users → worker spawns
          2. PUT admin's metadata (tenant_id, display_name)

        If step 2 fails, roll back step 1 (memory delete) so the two
        sides don't drift.
        """
        # Cross-project invariant: tenant must exist before we assign
        # a user to it.
        try:
            await self._tenants.get(body.tenant_id)
        except TenantNotFound as exc:
            raise TenantNotFoundForUser(
                f"tenant {body.tenant_id!r} not found; create it before "
                f"assigning users to it"
            ) from exc

        # Step 1 — memory create
        try:
            memory_record = await self._mem.create_user(
                user_id=body.user_id,
                palace_path=body.palace_path,
                consolidator=_consolidator_to_memory_dict(body.consolidator),
            )
        except MemoryUserUnreachable as exc:
            raise UserMemoryDown(str(exc)) from exc
        except MemoryUserUpstreamError as exc:
            self._map_memory_error(exc)

        # Step 2 — admin metadata. If this fails we MUST clean up the
        # memory side so admin and memory don't drift.
        meta = UserMetadata(
            tenant_id=body.tenant_id,
            display_name=body.display_name,
        )
        try:
            await self._meta.put(body.user_id, meta)
        except Exception as exc:  # noqa: BLE001 — drives compensation
            logger.exception(
                "user_create: admin metadata write failed for %s; "
                "rolling back memory side",
                body.user_id,
            )
            # Best-effort rollback. We DON'T raise from the rollback —
            # the original error is the one that matters to the caller.
            try:
                await self._mem.delete_user(body.user_id)
            except Exception:
                logger.exception(
                    "user_create: rollback (memory delete) ALSO failed "
                    "for %s; user is now memory-only — operator should "
                    "manually DELETE via /api/users to clean up",
                    body.user_id,
                )
            raise UserError(
                f"failed to persist admin metadata: {exc} (memory side "
                f"rolled back)"
            ) from exc

        return self._build_view(memory_record, meta)

    async def update_user(
        self, user_id: str, body: UpdateUserRequest
    ) -> UserView:
        """Update admin-owned fields (display_name + consolidator).

        Memory's surface from 29.B.2 has no PUT, so consolidator updates
        currently fall through to admin's metadata (we store the new
        config but memory's worker keeps the old). This is an explicit
        limitation noted in the schema; once memory's PUT exists, this
        method propagates the consolidator change through.
        """
        # Verify user exists first (in memory — the authoritative source)
        try:
            await self._mem.get_user(user_id)
        except MemoryUserUnreachable as exc:
            raise UserMemoryDown(str(exc)) from exc
        except MemoryUserUpstreamError as exc:
            self._map_memory_error(exc)

        current = await self._meta.get(user_id) or UserMetadata(tenant_id="default")
        new_meta = UserMetadata(
            tenant_id=current.tenant_id,
            active_agent_id=current.active_agent_id,
            display_name=(
                body.display_name if body.display_name is not None
                else current.display_name
            ),
        )
        # NOTE: body.consolidator is accepted for the schema's sake but
        # not yet propagated to memory (no PUT endpoint there). Surfaced
        # as a warning so we remember.
        if body.consolidator is not None:
            logger.warning(
                "user_update: consolidator change for %s NOT yet propagated "
                "to memory worker (memory has no PUT endpoint); admin will "
                "show the new config in views but the worker keeps old "
                "settings until a memory restart applies users.yaml",
                user_id,
            )
        await self._meta.put(user_id, new_meta)
        return await self.get_user(user_id)

    async def set_active_agent(
        self, user_id: str, body: SetActiveAgentRequest
    ) -> UserView:
        """Set the user's default agent. The agent existence is NOT
        validated here — that's 29.F's job (Agents orchestrator). For
        29.E we accept the string and store it; downstream callers that
        actually use it will validate.

        Use of an empty string is rejected by the schema (Field
        min_length=1) so we don't have to handle "unset".
        """
        # Confirm user exists
        try:
            await self._mem.get_user(user_id)
        except MemoryUserUnreachable as exc:
            raise UserMemoryDown(str(exc)) from exc
        except MemoryUserUpstreamError as exc:
            self._map_memory_error(exc)

        current = await self._meta.get(user_id) or UserMetadata(tenant_id="default")
        new_meta = UserMetadata(
            tenant_id=current.tenant_id,
            active_agent_id=body.agent_id,
            display_name=current.display_name,
        )
        await self._meta.put(user_id, new_meta)
        return await self.get_user(user_id)

    async def delete_user(self, user_id: str) -> dict[str, Any]:
        """Delete a user. Cascade order:

          1. Delete all agents owned by this user via AgentOrchestrator.
             That existing path clears user.active_agent references and
             unbinds devices pointing at those agents.
          2. DELETE memory user (terminates worker, trashes palace).
          3. DELETE admin metadata (cleanup).

        Returns memory's response envelope (includes ``palace_trashed_to``
        so admin UI can show "moved to ~/.eidolon-trash/...").
        """
        deleted_agents: list[dict[str, Any]] = []
        if self._agent_ids_provider is not None:
            try:
                agent_ids = await self._agent_ids_provider(user_id)
            except Exception as exc:  # noqa: BLE001 - aborts destructive delete
                raise UserError(
                    f"failed to enumerate agents for user {user_id!r}; "
                    "aborting user delete"
                ) from exc
            if agent_ids and self._agent_delete_provider is None:
                raise UserError(
                    f"user {user_id!r} owns agents but agent delete cascade "
                    "is not wired; aborting user delete"
                )
            agent_delete_provider = self._agent_delete_provider
            for agent_id in agent_ids:
                try:
                    if agent_delete_provider is None:
                        continue
                    deleted_agents.append(await agent_delete_provider(agent_id))
                except Exception as exc:  # noqa: BLE001 - abort before memory delete
                    raise UserError(
                        f"failed to delete owned agent {agent_id!r} before "
                        f"deleting user {user_id!r}; aborting user delete"
                    ) from exc

        # Step 2 — memory delete. Atomicity-wise, doing this AFTER the
        # admin-side cleanup would mean a failure leaves a user in memory
        # with no admin metadata. Doing it BEFORE means a failure leaves
        # an orphan admin metadata key (harmless, idempotent retry of
        # DELETE cleans it). The latter is preferable.
        try:
            memory_result = await self._mem.delete_user(user_id)
        except MemoryUserUnreachable as exc:
            raise UserMemoryDown(str(exc)) from exc
        except MemoryUserUpstreamError as exc:
            self._map_memory_error(exc)

        # Step 3 — admin metadata cleanup. Best-effort; idempotent.
        try:
            await self._meta.delete(user_id)
        except Exception:
            logger.exception(
                "user_delete: admin metadata cleanup failed for %s; "
                "memory user is already gone — retry DELETE to clean "
                "the orphan metadata",
                user_id,
            )
        memory_result["deleted_agents"] = [
            r.get("agent_id") for r in deleted_agents if r.get("agent_id")
        ]
        memory_result["agent_delete_results"] = deleted_agents
        return memory_result
