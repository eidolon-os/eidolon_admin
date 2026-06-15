"""Repository layer for Users.

Two stores composed in one module because admin always reads BOTH to
build a complete view:

  - :class:`MemoryUserClient` — HTTP client to memory's
    ``/api/admin/users/*`` (added in memory 29.B.2). This is the
    authoritative source for user existence + worker liveness + palace
    state.

  - :class:`UserMetadataRepository` — local SQLite storage for the few
    fields memory has no concept of: ``tenant_id``, ``active_agent_id``,
    ``display_name``. Keyed by user_id.

Each is its own thin layer (mirrors the Tenants / Templates pattern);
the orchestrator composes both.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .._shared import (
    SubProjectHTTPClient,
    SubProjectUnreachable,
    SubProjectUpstreamError,
)

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


# ===== admin's per-user metadata store ======================================


@dataclass
class UserMetadata:
    """What admin stores on top of memory's user record.

    Persisted in admin's local registry SQLite database.
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
    """SQLite-backed admin metadata store.

    Lives separately from memory's user data — memory is still the source
    of truth for "does user X exist", while admin's local registry DB is
    the source of truth for "is user X in tenant T with active agent A".
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path).expanduser()
        self._init_db()

    @property
    def db_path(self) -> Path:
        return self._db_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self._db_path,
            timeout=10.0,
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=10000")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_metadata (
                    user_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    active_agent_id TEXT,
                    display_name TEXT NOT NULL DEFAULT ''
                )
                """
            )

    async def _run(self, func):
        return await asyncio.to_thread(func)

    async def get(self, user_id: str) -> UserMetadata | None:
        def _get() -> UserMetadata | None:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT tenant_id, active_agent_id, display_name
                    FROM user_metadata
                    WHERE user_id = ?
                    """,
                    (user_id,),
                ).fetchone()
            if row is None:
                return None
            return UserMetadata(
                tenant_id=row["tenant_id"],
                active_agent_id=row["active_agent_id"],
                display_name=row["display_name"] or "",
            )

        return await self._run(_get)

    async def put(self, user_id: str, meta: UserMetadata) -> None:
        def _put() -> None:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO user_metadata (
                        user_id, tenant_id, active_agent_id, display_name
                    )
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                        tenant_id = excluded.tenant_id,
                        active_agent_id = excluded.active_agent_id,
                        display_name = excluded.display_name
                    """,
                    (
                        user_id,
                        meta.tenant_id,
                        meta.active_agent_id,
                        meta.display_name,
                    ),
                )

        await self._run(_put)

    async def delete(self, user_id: str) -> None:
        """Idempotent — deleting a non-existent key is a no-op."""
        def _delete() -> None:
            with self._connect() as conn:
                conn.execute(
                    "DELETE FROM user_metadata WHERE user_id = ?",
                    (user_id,),
                )

        await self._run(_delete)

    async def list_all(self) -> dict[str, UserMetadata]:
        """Return the full per-user map (user_id -> metadata).

        Returned as a dict for cheap "do I have this user?" lookups in
        the orchestrator's list path (which joins memory list × admin
        map by user_id).
        """
        def _list_all() -> dict[str, UserMetadata]:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT user_id, tenant_id, active_agent_id, display_name
                    FROM user_metadata
                    ORDER BY user_id
                    """
                ).fetchall()
            return {
                row["user_id"]: UserMetadata(
                    tenant_id=row["tenant_id"],
                    active_agent_id=row["active_agent_id"],
                    display_name=row["display_name"] or "",
                )
                for row in rows
            }

        return await self._run(_list_all)
