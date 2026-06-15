"""Tenant repository adapter.

Admin keeps the same repository API, but the SQLite implementation lives in
eidolon_sdk so the registry schema can be shared by other Python projects.
"""

from __future__ import annotations

from pathlib import Path

from eidolon_sdk.adapters.registry_sqlite import (
    RegistrySqliteStore,
    TenantRepository as SdkTenantRepository,
)

from ..schemas.tenant import TenantSpec


class TenantRepository:
    """Admin-facing thin wrapper over the SDK tenant store."""

    def __init__(self, db_path: str | Path) -> None:
        self._store = RegistrySqliteStore(db_path)
        self._repo = SdkTenantRepository(self._store)

    @property
    def db_path(self) -> Path:
        return self._store.db_path

    async def get(self, tenant_id: str) -> TenantSpec | None:
        return await self._repo.get(tenant_id)

    async def put(self, spec: TenantSpec) -> None:
        await self._repo.put(spec)

    async def delete(self, tenant_id: str) -> None:
        await self._repo.delete(tenant_id)

    async def list_all(self) -> list[TenantSpec]:
        return await self._repo.list_all()

    async def count(self) -> int:
        return await self._repo.count()

