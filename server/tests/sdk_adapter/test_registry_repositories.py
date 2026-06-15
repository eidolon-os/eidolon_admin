from __future__ import annotations

from datetime import datetime, timezone

from eidolon_sdk.adapters.registry_sqlite import (
    RegistrySqliteStore,
    TenantRepository,
    UserRepository,
)
from eidolon_sdk.registry.models import (
    ConsolidatorConfig,
    TenantSpec,
    UserRegistryRecord,
)


async def test_admin_can_use_sdk_tenant_repository_directly(tmp_path) -> None:
    store = RegistrySqliteStore(tmp_path / "registry.sqlite3")
    repo = TenantRepository(store)
    spec = TenantSpec(
        tenant_id="default",
        display_name="Default",
        created_at=datetime.now(timezone.utc),
    )

    await repo.put(spec)

    assert await repo.get("default") == spec
    assert await repo.count() == 1
    assert await repo.list_all() == [spec]


async def test_admin_can_use_sdk_user_repository_directly(tmp_path) -> None:
    store = RegistrySqliteStore(tmp_path / "registry.sqlite3")
    repo = UserRepository(store)
    record = UserRegistryRecord(
        user_id="alice",
        tenant_id="default",
        active_agent_id="ag-1",
        display_name="Alice",
        enabled=False,
        palace_path="/tmp/alice",
        memory_port=8030,
        consolidator=ConsolidatorConfig(
            enabled=False,
            interval_hours=4.0,
            window_days=8,
            min_drawers=2,
            min_confidence=0.7,
        ),
        created_at="2026-06-15T00:00:00+00:00",
    )

    await repo.put(record)

    assert await repo.get("alice") == record
    assert await repo.list_all() == {"alice": record}
    assert await repo.allocate_memory_port() == 8031
