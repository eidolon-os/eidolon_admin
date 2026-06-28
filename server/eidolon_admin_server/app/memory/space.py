"""Admin-memory realm identity helpers."""

from __future__ import annotations

from eidolon_sdk.memory import (
    MemoryActorContext,
    build_memory_actor_context,
    derive_memory_space_id,
)
from fastapi import HTTPException, Request

from .runners import RealmEntry, load_realms


def resolve_realm(memory_realm_id: str) -> RealmEntry:
    """Return the configured memory realm or raise a HTTP-friendly 404."""

    for realm in load_realms():
        if realm.memory_realm_id == memory_realm_id:
            return realm
    raise HTTPException(404, f"memory realm not found: {memory_realm_id!r}")


async def memory_space_id_for_realm(
    request: Request,
    memory_realm_id: str,
) -> str:
    """Return the memory-space id for an admin-addressed realm."""

    del request
    resolve_realm(memory_realm_id)
    return derive_memory_space_id(memory_realm_id)


async def memory_actor_context_for_realm(
    request: Request,
    memory_realm_id: str,
    *,
    device_id: str | None = None,
    session_id: str | None = None,
) -> MemoryActorContext:
    """Build the canonical memory context for admin read/write calls."""

    del request
    realm = resolve_realm(memory_realm_id)
    return build_memory_actor_context(
        owner_id=realm.owner_id,
        companion_id=realm.companion_id,
        memory_realm_id=realm.memory_realm_id,
        device_id=device_id,
        session_id=session_id,
    )
