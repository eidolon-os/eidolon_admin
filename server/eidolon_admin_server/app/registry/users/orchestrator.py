"""Users orchestration.

Admin's registry DB is the project-wide user source of truth. Memory reads
that registry and executes runtime state, while admin best-effort enriches
views with memory health.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from eidolon_sdk.core.http import ServiceUnavailable, ServiceUpstreamError
from eidolon_sdk.memory import derive_memory_space_id
from eidolon_sdk.biz.registry.models import UserRegistryRecord

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
from .repository import MemoryUserClient

logger = logging.getLogger(__name__)

_USER_LIFECYCLE_VERIFY_TIMEOUT_S = 600.0
_USER_LIFECYCLE_VERIFY_POLL_S = 1.0


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


class UserLifecycleVerifyFailed(UserError):
    """Create/delete did not converge to the expected final state."""

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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Admin has no persona concept yet; memory derives the space for an
# admin-registered user with this default persona. Keep in lockstep with
# memory-supervisor's space derivation for admin users.
_DEFAULT_PERSONA_ID = "default"


def _memory_space_id(tenant_id: str, user_id: str) -> str:
    """Forward join key: admin keys users by bare ``user_id``; memory-supervisor
    keys its runtime/health records by ``memory_space_id``
    (``<tenant>.<owner_user>.<persona>``). Use this whenever we address a single
    user on memory's admin surface."""
    return derive_memory_space_id(tenant_id, user_id, _DEFAULT_PERSONA_ID)


def _owner_user_id_from_space(space_id: str) -> str | None:
    """Reverse join key: extract ``owner_user_id`` from a ``memory_space_id``.
    Used when reading memory records back (we don't have the persona/tenant of a
    just-deleted user, and this needs no persona assumption)."""
    parts = (space_id or "").split(".")
    return parts[1] if len(parts) == 3 else None


# ---- orchestrator ----------------------------------------------------------


class UserOrchestrator:
    # Type alias for the agent-ids lookup hook. Returns the list of
    # agent_ids owned by a user. Wired by main.py after both the user
    # and agent orchestrators exist (setter-injection avoids the
    # circular import between users/agents).
    AgentIdsProvider = Callable[[str], Awaitable[list[str]]]
    AgentDeleteProvider = Callable[[str], Awaitable[dict[str, Any]]]
    AgentUserDataDeleteProvider = Callable[[str], Awaitable[dict[str, Any]]]
    VoiceprintDeleteProvider = Callable[[str, str], Awaitable[bool]]

    def __init__(
        self,
        *,
        memory_client: MemoryUserClient,
        metadata_repo: Any,
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
        self._agent_user_data_delete_provider: (
            UserOrchestrator.AgentUserDataDeleteProvider | None
        ) = None
        self._voiceprint_delete_provider: (
            UserOrchestrator.VoiceprintDeleteProvider | None
        ) = None

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

    def set_agent_user_data_delete_provider(
        self, provider: AgentUserDataDeleteProvider
    ) -> None:
        """Inject agent-project hard deletion for all user-owned data."""
        self._agent_user_data_delete_provider = provider

    def set_voiceprint_delete_provider(
        self, provider: VoiceprintDeleteProvider
    ) -> None:
        """Inject voiceprint profile/enrollment deletion."""
        self._voiceprint_delete_provider = provider

    # ---- internal helpers ----------------------------------------------

    def _build_view(
        self,
        user_id: str,
        admin_record: UserRegistryRecord,
        memory_record: dict[str, Any] | None = None,
    ) -> UserView:
        """Compose UserView with admin registry as the spine."""
        record = memory_record or {}
        health_block = record.get("health", {})
        mcp_http_url = record.get("mcp_http_url", "")
        if not mcp_http_url and admin_record.memory_port > 0:
            mcp_http_url = f"http://127.0.0.1:{admin_record.memory_port}/mcp"
        spec = UserSpec(
            user_id=user_id,
            tenant_id=admin_record.tenant_id,
            display_name=admin_record.display_name or user_id,
            enabled=admin_record.enabled,
            memory_port=admin_record.memory_port,
            palace_path=admin_record.palace_path,
            consolidator=admin_record.consolidator,
            created_at=_parse_dt(admin_record.created_at),
        )
        health = UserHealth(
            worker_running=bool(health_block.get("worker_running")),
            mcp_reachable=bool(health_block.get("mcp_reachable")),
            palace_initialized=bool(health_block.get("palace_initialized")),
            note=health_block.get("note", "") if memory_record else "memory unavailable",
        )
        return UserView(
            spec=spec,
            health=health,
            active_agent_id=admin_record.active_agent_id,
            # agent_ids: empty here; callers that have the provider wired
            # (list_users / get_user) decorate after _build_view returns.
            # Direct callers without an agent_orchestrator (tests, edge
            # paths) get an empty list — accurate, not "TODO later".
            agent_ids=[],
            mcp_http_url=mcp_http_url,
        )

    def _map_memory_error(self, exc: ServiceUpstreamError) -> None:
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

    async def _memory_records_by_id(self) -> dict[str, dict[str, Any]]:
        try:
            envelope = await self._mem.list_users()
        except (ServiceUnavailable, ServiceUpstreamError):
            logger.info("users: memory health enrichment unavailable", exc_info=True)
            return {}
        out: dict[str, dict[str, Any]] = {}
        for record in envelope.get("users", []) or []:
            try:
                # memory keys ``spec.user_id`` by memory_space_id; admin joins
                # by bare owner_user_id.
                owner = _owner_user_id_from_space(record["spec"]["user_id"])
                if owner is None:
                    continue
                out[owner] = record
            except Exception:  # noqa: BLE001
                continue
        return out

    async def _reconcile_memory(self, *, strict: bool = False) -> None:
        try:
            await self._mem.reconcile()
        except Exception as exc:  # noqa: BLE001
            if strict:
                raise UserMemoryDown(
                    "memory reconcile request failed; user lifecycle did not converge"
                ) from exc
            logger.warning(
                "users: memory reconcile request failed; supervisor will catch up on restart/reload",
                exc_info=True,
            )

    def _create_health_failures(
        self, *, record: dict[str, Any], enabled: bool
    ) -> list[str]:
        health = record.get("health") or {}
        failures: list[str] = []
        if not health.get("palace_initialized"):
            failures.append("palace_initialized=false")
        if enabled:
            if not health.get("worker_running"):
                failures.append("worker_running=false")
            if not health.get("mcp_reachable"):
                failures.append("mcp_reachable=false")
        return failures

    async def _wait_for_user_create_verified(
        self,
        user_id: str,
        *,
        enabled: bool,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        if timeout_s is None:
            timeout_s = _USER_LIFECYCLE_VERIFY_TIMEOUT_S
        deadline = time.monotonic() + timeout_s
        last_detail = "not checked"
        while True:
            meta = await self._meta.get(user_id)
            if meta is None:
                last_detail = "admin registry row missing"
            else:
                try:
                    record = await self._mem.get_user(
                        _memory_space_id(meta.tenant_id, user_id)
                    )
                    failures = self._create_health_failures(
                        record=record,
                        enabled=enabled,
                    )
                    if not failures:
                        return record
                    last_detail = ", ".join(failures)
                except ServiceUnavailable as exc:
                    last_detail = f"memory unavailable: {exc}"
                except ServiceUpstreamError as exc:
                    last_detail = f"memory returned {exc.status_code}: {exc.message}"

            if time.monotonic() >= deadline:
                raise UserLifecycleVerifyFailed(
                    f"user {user_id!r} create did not verify within "
                    f"{timeout_s:.0f}s: {last_detail}"
                )
            await asyncio.sleep(_USER_LIFECYCLE_VERIFY_POLL_S)

    async def _wait_for_user_delete_verified(
        self,
        user_id: str,
        *,
        timeout_s: float | None = None,
    ) -> None:
        if timeout_s is None:
            timeout_s = _USER_LIFECYCLE_VERIFY_TIMEOUT_S
        deadline = time.monotonic() + timeout_s
        last_detail = "not checked"
        while True:
            admin_missing = await self._meta.get(user_id) is None
            if not admin_missing:
                last_detail = "admin registry row still exists"
            else:
                try:
                    envelope = await self._mem.list_users()
                    users = envelope.get("users", []) or []
                    # memory keys ``spec.user_id`` by memory_space_id; reduce to
                    # owner_user_id to compare against admin's bare user_id.
                    memory_owner_ids = {
                        _owner_user_id_from_space(
                            str((record.get("spec") or {}).get("user_id"))
                        )
                        for record in users
                        if isinstance(record, dict)
                    }
                    if user_id in memory_owner_ids:
                        last_detail = "memory registry still lists user"
                    else:
                        provider = self._agent_ids_provider
                        agent_ids = await provider(user_id) if provider else []
                        if agent_ids:
                            last_detail = f"owned agents still exist: {agent_ids}"
                        else:
                            return
                except ServiceUnavailable as exc:
                    last_detail = f"memory unavailable: {exc}"
                except ServiceUpstreamError as exc:
                    last_detail = f"memory returned {exc.status_code}: {exc.message}"

            if time.monotonic() >= deadline:
                raise UserLifecycleVerifyFailed(
                    f"user {user_id!r} delete did not verify within "
                    f"{timeout_s:.0f}s: {last_detail}"
                )
            await asyncio.sleep(_USER_LIFECYCLE_VERIFY_POLL_S)

    def _copy_record(
        self, record: UserRegistryRecord, **updates: Any
    ) -> UserRegistryRecord:
        data = record.model_dump()
        data.update(updates)
        return UserRegistryRecord(**data)

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
                await self._meta.put(
                    self._copy_record(meta, active_agent_id=None),
                )
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

        Implementation note: reads admin's own metadata store (the
        authoritative source for tenant↔user mapping). Doesn't hit
        memory — if memory has a user without an admin metadata entry,
        that user is by definition in the default tenant fallback and
        wouldn't block a non-default tenant deletion.
        """
        all_meta = await self._meta.list_all()
        return sum(1 for m in all_meta.values() if m.tenant_id == tenant_id)

    async def list_users(self) -> list[UserView]:
        """List users from admin DB and best-effort attach memory health."""
        meta_map = await self._meta.list_all()
        memory_map = await self._memory_records_by_id()
        views = [
            self._build_view(user_id, meta, memory_map.get(user_id))
            for user_id, meta in meta_map.items()
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

    async def list_registry_specs(self) -> list[UserSpec]:
        """Pure admin-registry view for execution projects.

        This deliberately does not call memory. Memory reads this endpoint to
        avoid a recursion loop where admin /api/users enriches via memory and
        memory asks admin /api/users again.
        """
        meta_map = await self._meta.list_all()
        specs: list[UserSpec] = []
        for user_id, meta in meta_map.items():
            specs.append(
                UserSpec(
                    user_id=user_id,
                    tenant_id=meta.tenant_id,
                    display_name=meta.display_name or user_id,
                    enabled=meta.enabled,
                    memory_port=meta.memory_port,
                    palace_path=meta.palace_path,
                    consolidator=meta.consolidator,
                    created_at=_parse_dt(meta.created_at),
                )
            )
        return specs

    async def get_user(self, user_id: str) -> UserView:
        meta = await self._meta.get(user_id)
        if meta is None:
            raise UserNotFound(f"user {user_id!r} not found")
        record: dict[str, Any] | None = None
        try:
            record = await self._mem.get_user(_memory_space_id(meta.tenant_id, user_id))
        except ServiceUnavailable as exc:
            logger.info("users: memory get unavailable for %s: %s", user_id, exc)
        except ServiceUpstreamError as exc:
            if exc.status_code != 404:
                logger.info("users: memory get failed for %s", user_id, exc_info=True)
        view = self._build_view(user_id, meta, record)
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
        """Create user in admin registry, then ask memory to reconcile."""
        # Cross-project invariant: tenant must exist before we assign
        # a user to it.
        try:
            await self._tenants.get(body.tenant_id)
        except TenantNotFound as exc:
            raise TenantNotFoundForUser(
                f"tenant {body.tenant_id!r} not found; create it before "
                f"assigning users to it"
            ) from exc

        if await self._meta.get(body.user_id) is not None:
            raise UserAlreadyExists(f"user {body.user_id!r} already exists")

        memory_port = await self._meta.allocate_memory_port()
        record = UserRegistryRecord(
            user_id=body.user_id,
            tenant_id=body.tenant_id,
            display_name=body.display_name,
            enabled=body.enabled,
            palace_path=body.palace_path,
            memory_port=memory_port,
            consolidator=body.consolidator,
            created_at=_now_iso(),
        )
        await self._meta.put(record)
        await self._reconcile_memory(strict=True)
        await self._wait_for_user_create_verified(
            body.user_id,
            enabled=body.enabled,
        )

        return await self.get_user(body.user_id)

    async def update_user(
        self, user_id: str, body: UpdateUserRequest
    ) -> UserView:
        """Update admin-owned user fields and ask memory to reconcile."""
        current = await self._meta.get(user_id)
        if current is None:
            raise UserNotFound(f"user {user_id!r} not found")
        updates: dict[str, Any] = {}
        if body.display_name is not None:
            updates["display_name"] = body.display_name
        if body.consolidator is not None:
            updates["consolidator"] = body.consolidator
        new_record = self._copy_record(current, **updates)
        await self._meta.put(new_record)
        await self._reconcile_memory()
        return await self.get_user(user_id)

    async def set_user_enabled(self, user_id: str, enabled: bool) -> UserView:
        current = await self._meta.get(user_id)
        if current is None:
            raise UserNotFound(f"user {user_id!r} not found")
        await self._meta.put(self._copy_record(current, enabled=enabled))
        await self._reconcile_memory()
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
        current = await self._meta.get(user_id)
        if current is None:
            raise UserNotFound(f"user {user_id!r} not found")
        new_record = self._copy_record(current, active_agent_id=body.agent_id)
        await self._meta.put(new_record)
        return await self.get_user(user_id)

    async def delete_user(self, user_id: str) -> dict[str, Any]:
        """Delete a user. Cascade order:

          1. Delete all agents owned by this user via AgentOrchestrator.
             That existing path clears user.active_agent references and
             unbinds devices pointing at those agents.
          2. Delete agent-project data keyed directly by user_id
             (conversation history, long tasks, orphan persona rows, etc.).
          3. Delete voiceprint enrollment/profile data.
          4. DELETE memory user (terminates worker, trashes palace).
          5. DELETE admin metadata (cleanup).

        Returns memory's response envelope (includes ``palace_trashed_to``
        so admin UI can show "moved to ~/.eidolon-trash/...").
        """
        current = await self._meta.get(user_id)
        if current is None:
            raise UserNotFound(f"user {user_id!r} not found")

        if (
            self._agent_ids_provider is None
            or self._agent_delete_provider is None
            or self._agent_user_data_delete_provider is None
            or self._voiceprint_delete_provider is None
        ):
            raise UserError(
                "full user delete is not fully wired; refusing to delete "
                f"user {user_id!r} because related data could be orphaned"
            )

        deleted_agents: list[dict[str, Any]] = []
        try:
            agent_ids = await self._agent_ids_provider(user_id)
        except Exception as exc:  # noqa: BLE001 - aborts destructive delete
            raise UserError(
                f"failed to enumerate agents for user {user_id!r}; "
                "aborting user delete"
            ) from exc
        agent_delete_provider = self._agent_delete_provider
        for agent_id in agent_ids:
            try:
                deleted_agents.append(await agent_delete_provider(agent_id))
            except Exception as exc:  # noqa: BLE001 - abort before memory delete
                raise UserError(
                    f"failed to delete owned agent {agent_id!r} before "
                    f"deleting user {user_id!r}; aborting user delete"
                ) from exc

        try:
            agent_data_result = await self._agent_user_data_delete_provider(user_id)
        except Exception as exc:  # noqa: BLE001 - abort before memory delete
            raise UserError(
                f"failed to delete agent-owned data for user {user_id!r}; "
                "aborting user delete"
            ) from exc

        try:
            voiceprint_deleted = await self._voiceprint_delete_provider(
                current.tenant_id,
                user_id,
            )
        except Exception as exc:  # noqa: BLE001 - abort before memory delete
            raise UserError(
                f"failed to delete voiceprint data for user {user_id!r}; "
                "aborting user delete"
            ) from exc

        # Disable first in the admin registry. Memory only executes the
        # registry state, so this is the single project-wide stop switch.
        if current.enabled:
            await self._meta.put(self._copy_record(current, enabled=False))
            await self._reconcile_memory()

        # Ask memory to clean up memory-owned data. This is a lifecycle
        # barrier: if purge fails, keep admin metadata so the operator can
        # retry without losing the source-of-truth row.
        try:
            memory_result = await self._mem.delete_user(
                _memory_space_id(current.tenant_id, user_id)
            )
        except ServiceUnavailable as exc:
            raise UserMemoryDown(
                f"memory unavailable while deleting user {user_id!r}; "
                "admin metadata was kept so delete can be retried"
            ) from exc
        except ServiceUpstreamError as exc:
            raise UserError(
                f"memory cleanup failed while deleting user {user_id!r}: "
                f"{exc.status_code}: {unwrap_detail(exc.message)}"
            ) from exc

        await self._meta.delete(user_id)
        await self._reconcile_memory(strict=True)
        await self._wait_for_user_delete_verified(user_id)
        memory_result["deleted_agents"] = [
            r.get("agent_id") for r in deleted_agents if r.get("agent_id")
        ]
        memory_result["agent_delete_results"] = deleted_agents
        memory_result["agent_user_data_delete_result"] = agent_data_result
        memory_result["voiceprint_deleted"] = voiceprint_deleted
        return memory_result
