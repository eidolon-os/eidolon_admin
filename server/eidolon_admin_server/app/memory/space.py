"""Admin↔memory join-key helpers.

memory-supervisor keys users by ``memory_space_id``
(``<tenant>.<owner_user>.<persona>``); admin keys by bare ``user_id``. Any admin
code that addresses a single user on memory's surface must translate. Admin has
no persona concept yet, so admin-registered users live in the default-persona
space — matching memory-supervisor's own derivation.
"""
from __future__ import annotations

from fastapi import Request

from eidolon_sdk.memory import derive_memory_space_id

DEFAULT_PERSONA_ID = "default"


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


async def memory_space_id_for_user(request: Request, user_id: str) -> str:
    """The memory_space_id memory-supervisor keys ``user_id`` by."""
    tenant_id = await tenant_for_user(request, user_id)
    return derive_memory_space_id(tenant_id, user_id, DEFAULT_PERSONA_ID)
