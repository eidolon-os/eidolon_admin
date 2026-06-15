"""Repository layer for Users.

Two stores composed in one module because admin always reads BOTH to
build a complete view:

  - :class:`MemoryUserClient` — HTTP client to memory's
    ``/api/admin/users/*`` (added in memory 29.B.2). This is the
    authoritative source for user existence + worker liveness + palace
    state.

  - :class:`UserMetadataRepository` — NATS KV adapter over admin's
    ``USERS_METADATA_BUCKET``. Stores the few fields memory has no
    concept of: ``tenant_id``, ``active_agent_id``. Keyed by user_id.

Each is its own thin layer (mirrors the Tenants / Templates pattern);
the orchestrator composes both.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from ...nats_kv import KVClient, from_json_bytes, to_json_bytes
from .._shared import (
    SubProjectHTTPClient,
    SubProjectUnreachable,
    SubProjectUpstreamError,
)
from ..buckets import USERS_METADATA_BUCKET
from ..keys import user_metadata_key

logger = logging.getLogger(__name__)


# ===== memory HTTP client ===================================================
#
# Backwards-compatible aliases so the orchestrator + tests keep
# importing the old names. The shared base classes ARE these classes —
# we're just giving them domain-specific names.

MemoryUserUnreachable = SubProjectUnreachable
MemoryUserUpstreamError = SubProjectUpstreamError


class MemoryUserClient(SubProjectHTTPClient):
    """HTTP wrapper over memory's user CRUD surface.

    Memory exposes these on its supervisor-embedded HTTP (default
    ``http://127.0.0.1:8019``, set via ``supervisor.admin_http_port``):

        GET    /api/admin/users
        GET    /api/admin/users/{user_id}
        POST   /api/admin/users
        DELETE /api/admin/users/{user_id}

    There is no PUT — memory's surface from 29.B.2 doesn't update meta.
    Admin's "update" is therefore limited to admin-owned fields
    (tenant_id, active_agent_id) — the orchestrator enforces that.
    """

    async def list_users(self) -> dict[str, Any]:
        """Returns the full envelope (users list + memory_available)."""
        r = await self._request("GET", "/api/admin/users")
        return r.json()

    async def get_user(self, user_id: str) -> dict[str, Any]:
        r = await self._request("GET", f"/api/admin/users/{user_id}")
        return r.json()

    async def create_user(
        self,
        *,
        user_id: str,
        enabled: bool = False,
        display_name: str | None = None,
        palace_path: str = "",
        consolidator: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "user_id": user_id,
            "enabled": enabled,
            "palace_path": palace_path,
        }
        if consolidator is not None:
            body["consolidator"] = consolidator
        # memory's CreateUserRequest has no display_name field — that's
        # an admin-side concept. We send only the fields memory accepts.
        # Create can block on memory's reconcile/palace init path
        # (60s palace init + worker wait). Keep this longer than the
        # normal control-plane timeout so admin doesn't report failure
        # after memory already wrote users.yaml.
        r = await self._request(
            "POST",
            "/api/admin/users",
            json=body,
            timeout=120.0,
        )
        return r.json()

    async def delete_user(self, user_id: str) -> dict[str, Any]:
        """Returns memory's response envelope (includes
        ``palace_trashed_to`` when applicable)."""
        r = await self._request(
            "DELETE",
            f"/api/admin/users/{user_id}",
            timeout=120.0,
        )
        return r.json()


# ===== admin's per-user metadata KV =========================================


@dataclass
class UserMetadata:
    """What admin stores on top of memory's user record.

    Persisted as a small JSON object in NATS KV. Kept tiny so the bucket
    cap (4 KB) is never an issue.
    """

    tenant_id: str
    active_agent_id: str | None = None
    display_name: str = ""  # admin-side label; memory doesn't have one

    def to_json(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "active_agent_id": self.active_agent_id,
            "display_name": self.display_name,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "UserMetadata":
        return cls(
            tenant_id=data.get("tenant_id", "default"),
            active_agent_id=data.get("active_agent_id"),
            display_name=data.get("display_name", ""),
        )


class UserMetadataRepository:
    """KV-backed admin metadata store.

    Lives separately from memory's user data — memory is the source of
    truth for "does user X exist", admin's KV is the source of truth
    for "is user X in tenant T with active agent A".
    """

    def __init__(self, kv: KVClient) -> None:
        self._kv = kv

    async def get(self, user_id: str) -> UserMetadata | None:
        raw = await self._kv.get(USERS_METADATA_BUCKET.name, user_metadata_key(user_id))
        if raw is None:
            return None
        try:
            return UserMetadata.from_json(from_json_bytes(raw))
        except Exception:
            logger.exception("users: malformed KV entry %s", user_id)
            return None

    async def put(self, user_id: str, meta: UserMetadata) -> None:
        await self._kv.put(
            USERS_METADATA_BUCKET.name,
            user_metadata_key(user_id),
            to_json_bytes(meta.to_json()),
        )

    async def delete(self, user_id: str) -> None:
        """Idempotent — deleting a non-existent key is a no-op."""
        await self._kv.delete(USERS_METADATA_BUCKET.name, user_metadata_key(user_id))

    async def list_all(self) -> dict[str, UserMetadata]:
        """Return the full per-user map (user_id → metadata).

        Returned as a dict for cheap "do I have this user?" lookups in
        the orchestrator's list path (which joins memory list × admin
        map by user_id).
        """
        keys = await self._kv.list_keys(USERS_METADATA_BUCKET.name, prefix="user.")
        out: dict[str, UserMetadata] = {}
        for key in keys:
            raw = await self._kv.get(USERS_METADATA_BUCKET.name, key)
            if raw is None:
                continue
            try:
                meta = UserMetadata.from_json(from_json_bytes(raw))
            except Exception:
                logger.exception("users: malformed KV entry at key %s", key)
                continue
            # Key shape is ``user.<id>``; strip the prefix.
            user_id = key.removeprefix("user.")
            out[user_id] = meta
        return out
