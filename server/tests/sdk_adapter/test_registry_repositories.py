from __future__ import annotations

from datetime import datetime, timezone

from eidolon_admin_server.app.registry.schemas.tenant import TenantSpec
from eidolon_admin_server.app.registry.tenants import TenantRepository
from eidolon_admin_server.app.registry.users.repository import (
    UserMetadata,
    UserMetadataRepository,
)


async def test_admin_tenant_repository_keeps_public_api(tmp_path) -> None:
    repo = TenantRepository(tmp_path / "registry.sqlite3")
    spec = TenantSpec(
        tenant_id="default",
        display_name="Default",
        created_at=datetime.now(timezone.utc),
    )

    await repo.put(spec)

    assert await repo.get("default") == spec
    assert await repo.count() == 1
    assert await repo.list_all() == [spec]


async def test_admin_user_metadata_repository_keeps_public_api(tmp_path) -> None:
    repo = UserMetadataRepository(tmp_path / "registry.sqlite3")
    meta = UserMetadata(
        tenant_id="default",
        active_agent_id="ag-1",
        display_name="Alice",
        enabled=False,
        palace_path="/tmp/alice",
        memory_port=8030,
        consolidator_enabled=False,
        consolidator_interval_hours=4.0,
        consolidator_window_days=8,
        consolidator_min_drawers=2,
        consolidator_min_confidence=0.7,
        created_at="2026-06-15T00:00:00+00:00",
    )

    await repo.put("alice", meta)

    assert await repo.get("alice") == meta
    assert await repo.list_all() == {"alice": meta}
    assert await repo.allocate_memory_port() == 8031

