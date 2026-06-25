"""Admin-memory identity helpers.

Admin product surfaces use ``companion_id``. The memory wire contract still
uses ``persona_id`` for the same product concept.
"""
from __future__ import annotations

from eidolon_sdk.memory import MemoryActorContext, derive_memory_space_id
from fastapi import Request

DEFAULT_COMPANION_ID = "default"
DEFAULT_PERSONA_ID = DEFAULT_COMPANION_ID
ADMIN_MEMORY_ACTOR_ID = "admin_ui"


async def tenant_for_user(
    request: Request, user_id: str, *, default: str = "default"
) -> str:
    """Resolve a user's tenant from the admin registry (no memory round-trip).
    Best-effort: falls back to ``default`` when the orchestrator is unwired or
    the user is unknown."""
    orch = getattr(request.app.state, "user_orchestrator", None)
    if orch is None:
        return default
    try:
        specs = await orch.list_registry_specs()
    except Exception:  # noqa: BLE001 — registry read is best-effort here
        return default
    spec = next((s for s in specs if s.user_id == user_id), None)
    return spec.tenant_id if spec else default


async def memory_space_id_for_user(
    request: Request,
    user_id: str,
    *,
    companion_id: str | None = None,
) -> str:
    """Return the memory-space id for an admin-addressed user."""
    tenant_id = await tenant_for_user(request, user_id)
    return derive_memory_space_id(
        tenant_id,
        user_id,
        companion_id or DEFAULT_COMPANION_ID,
    )


async def memory_actor_context_for_user(
    request: Request,
    user_id: str,
    *,
    companion_id: str | None = None,
    agent_id: str = ADMIN_MEMORY_ACTOR_ID,
    device_id: str = ADMIN_MEMORY_ACTOR_ID,
    instance_id: str = ADMIN_MEMORY_ACTOR_ID,
    session_id: str = ADMIN_MEMORY_ACTOR_ID,
) -> MemoryActorContext:
    """Build the canonical memory context for admin read/write calls."""

    tenant_id = await tenant_for_user(request, user_id)
    return MemoryActorContext(
        tenant_id=tenant_id,
        owner_user_id=user_id,
        persona_id=companion_id or DEFAULT_COMPANION_ID,
        agent_id=agent_id,
        device_id=device_id,
        instance_id=instance_id,
        session_id=session_id,
    )
