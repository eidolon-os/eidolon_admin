"""SQLite persistence for tenants.

Thin adapter between the orchestrator (which speaks Pydantic ``TenantSpec``)
and admin's local registry database. Only this layer knows the table shape;
everything above is implementation-agnostic about where tenants live.
"""
from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from ..schemas.tenant import TenantSpec


class TenantRepository:
    """SQLite-backed store for tenants."""

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
                CREATE TABLE IF NOT EXISTS tenants (
                    tenant_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

    async def _run(self, func):
        return await asyncio.to_thread(func)

    async def get(self, tenant_id: str) -> TenantSpec | None:
        """Return the tenant, or ``None`` if no key exists."""
        def _get() -> TenantSpec | None:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT tenant_id, display_name, created_at
                    FROM tenants
                    WHERE tenant_id = ?
                    """,
                    (tenant_id,),
                ).fetchone()
            if row is None:
                return None
            return TenantSpec.model_validate(dict(row))

        return await self._run(_get)

    async def put(self, spec: TenantSpec) -> None:
        """Persist (create or overwrite). Caller has already enforced
        uniqueness / mutability rules; this is a flat write."""
        data = spec.model_dump(mode="json")

        def _put() -> None:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO tenants (tenant_id, display_name, created_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(tenant_id) DO UPDATE SET
                        display_name = excluded.display_name,
                        created_at = excluded.created_at
                    """,
                    (
                        data["tenant_id"],
                        data["display_name"],
                        data["created_at"],
                    ),
                )

        await self._run(_put)

    async def delete(self, tenant_id: str) -> None:
        """Remove the key. Idempotent — deleting a non-existent key is fine."""
        def _delete() -> None:
            with self._connect() as conn:
                conn.execute(
                    "DELETE FROM tenants WHERE tenant_id = ?",
                    (tenant_id,),
                )

        await self._run(_delete)

    async def list_all(self) -> list[TenantSpec]:
        """Return every tenant. Order is database-natural (no sort applied);
        the router/orchestrator sorts by display_name if it cares."""
        def _list_all() -> list[TenantSpec]:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT tenant_id, display_name, created_at
                    FROM tenants
                    ORDER BY tenant_id
                    """
                ).fetchall()
            return [TenantSpec.model_validate(dict(row)) for row in rows]

        return await self._run(_list_all)

    async def count(self) -> int:
        """Number of tenants. Used by the "can't delete last tenant" guard
        and by the seed-default-on-empty bootstrap helper."""
        def _count() -> int:
            with self._connect() as conn:
                row = conn.execute("SELECT COUNT(*) AS n FROM tenants").fetchone()
            return int(row["n"])

        return await self._run(_count)
