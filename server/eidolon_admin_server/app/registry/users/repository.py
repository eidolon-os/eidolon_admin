"""Repository layer for Users.

Admin's SQLite registry is the only source of truth for user existence and
the project-wide enabled flag. The memory project consumes this table and
executes the resulting runtime state; it no longer owns a separate user
catalog.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from eidolon_sdk.adapters.registry_sqlite import (
    RegistrySqliteStore,
    UserRepository as SdkUserRepository,
)
from eidolon_sdk.registry.models import (
    ConsolidatorConfig as SdkConsolidatorConfig,
    UserRegistryRecord,
)

from eidolon_sdk.http import (
    ServiceHTTPClient,
    ServiceUnavailable,
    ServiceUpstreamError,
)

# ===== memory HTTP client ===================================================
#
# Domain-specific names for SDK transport errors.

MemoryUserUnreachable = ServiceUnavailable
MemoryUserUpstreamError = ServiceUpstreamError


class MemoryUserClient(ServiceHTTPClient):
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

    async def reconcile(self) -> dict[str, Any]:
        """Ask memory-supervisor to re-read the admin registry DB."""
        r = await self._request("POST", "/api/admin/reconcile", timeout=120.0)
        return r.json()


# ===== admin's per-user metadata store ======================================


@dataclass
class UserMetadata:
    """Admin-owned user registry row."""

    tenant_id: str
    active_agent_id: str | None = None
    display_name: str = ""  # admin-side label; memory doesn't have one
    enabled: bool = True
    palace_path: str = ""
    memory_port: int = 0
    consolidator_enabled: bool = True
    consolidator_interval_hours: float = 6.0
    consolidator_window_days: int = 30
    consolidator_min_drawers: int = 3
    consolidator_min_confidence: float = 0.6
    created_at: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "active_agent_id": self.active_agent_id,
            "display_name": self.display_name,
            "enabled": self.enabled,
            "palace_path": self.palace_path,
            "memory_port": self.memory_port,
            "consolidator_enabled": self.consolidator_enabled,
            "consolidator_interval_hours": self.consolidator_interval_hours,
            "consolidator_window_days": self.consolidator_window_days,
            "consolidator_min_drawers": self.consolidator_min_drawers,
            "consolidator_min_confidence": self.consolidator_min_confidence,
            "created_at": self.created_at,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "UserMetadata":
        return cls(
            tenant_id=data.get("tenant_id", "default"),
            active_agent_id=data.get("active_agent_id"),
            display_name=data.get("display_name", ""),
            enabled=bool(data.get("enabled", True)),
            palace_path=data.get("palace_path", "") or "",
            memory_port=int(data.get("memory_port", 0) or 0),
            consolidator_enabled=bool(data.get("consolidator_enabled", True)),
            consolidator_interval_hours=float(
                data.get("consolidator_interval_hours", 6.0) or 6.0
            ),
            consolidator_window_days=int(data.get("consolidator_window_days", 30) or 30),
            consolidator_min_drawers=int(data.get("consolidator_min_drawers", 3) or 3),
            consolidator_min_confidence=float(
                data.get("consolidator_min_confidence", 0.6) or 0.6
            ),
            created_at=data.get("created_at", "") or "",
        )


class UserMetadataRepository:
    """Admin-facing thin wrapper over the SDK user registry store."""

    def __init__(self, db_path: str | Path) -> None:
        self._store = RegistrySqliteStore(
            db_path,
            legacy_users_yaml_path=_legacy_users_yaml_path(),
        )
        self._repo = SdkUserRepository(self._store)

    @property
    def db_path(self) -> Path:
        return self._store.db_path

    async def get(self, user_id: str) -> UserMetadata | None:
        record = await self._repo.get(user_id)
        if record is None:
            return None
        return _record_to_meta(record)

    async def put(self, user_id: str, meta: UserMetadata) -> None:
        await self._repo.put(_meta_to_record(user_id, meta))

    async def delete(self, user_id: str) -> None:
        """Idempotent — deleting a non-existent key is a no-op."""
        await self._repo.delete(user_id)

    async def list_all(self) -> dict[str, UserMetadata]:
        """Return the full per-user map (user_id -> metadata).

        Returned as a dict for cheap "do I have this user?" lookups in
        the orchestrator's list path (which joins memory list × admin
        map by user_id).
        """
        records = await self._repo.list_all()
        return {user_id: _record_to_meta(record) for user_id, record in records.items()}

    async def allocate_memory_port(self) -> int:
        return await self._repo.allocate_memory_port()


def _record_to_meta(record: UserRegistryRecord) -> UserMetadata:
    return UserMetadata(
        tenant_id=record.tenant_id,
        active_agent_id=record.active_agent_id,
        display_name=record.display_name,
        enabled=record.enabled,
        palace_path=record.palace_path,
        memory_port=record.memory_port,
        consolidator_enabled=record.consolidator.enabled,
        consolidator_interval_hours=record.consolidator.interval_hours,
        consolidator_window_days=record.consolidator.window_days,
        consolidator_min_drawers=record.consolidator.min_drawers,
        consolidator_min_confidence=record.consolidator.min_confidence,
        created_at=record.created_at,
    )


def _meta_to_record(user_id: str, meta: UserMetadata) -> UserRegistryRecord:
    return UserRegistryRecord(
        user_id=user_id,
        tenant_id=meta.tenant_id,
        active_agent_id=meta.active_agent_id,
        display_name=meta.display_name,
        enabled=meta.enabled,
        palace_path=meta.palace_path,
        memory_port=meta.memory_port,
        consolidator=SdkConsolidatorConfig(
            enabled=meta.consolidator_enabled,
            interval_hours=meta.consolidator_interval_hours,
            window_days=meta.consolidator_window_days,
            min_drawers=meta.consolidator_min_drawers,
            min_confidence=meta.consolidator_min_confidence,
        ),
        created_at=meta.created_at,
    )


def _legacy_users_yaml_path() -> Path:
    raw = os.environ.get("EIDOLON_MEMORY_USERS_YAML", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    root = os.environ.get("EIDOLON_ROOT", "").strip()
    if root:
        base = Path(root).expanduser()
    else:
        base = Path(__file__).resolve().parents[6]
    return (base / "eidolon_memory" / "config" / "users.yaml").resolve()
