from __future__ import annotations

import pytest
from sqlalchemy import update

from eidolon_admin_server.app.registry.resolve.orchestrator import (
    ResolveDeviceNotBound,
    ResolveDeviceUnavailable,
    ResolveOrchestrator,
)
from eidolon_data import DataSettings, DataStore
from eidolon_data.schema.models import DeviceRow

pytestmark = pytest.mark.asyncio


async def _store(tmp_path) -> DataStore:
    store = DataStore.open(DataSettings(sqlite_path=str(tmp_path / "eidolon.sqlite3")))
    await store.init_schema()
    return store


async def _workspace(store: DataStore, *, owner_id: str = "owner-a"):
    await store.owner_service.create_owner(owner_id=owner_id, display_name=owner_id)
    return await store.companion_workspace.initialize_workspace(
        owner_id=owner_id,
        companion_id=f"c:{owner_id}:main",
        genome_id=f"g:{owner_id}:main:v1",
        realm_id=f"r:{owner_id}:main",
    )


async def test_resolve_device_returns_owner_companion_runtime_identity(tmp_path) -> None:
    store = await _store(tmp_path)
    try:
        workspace = await _workspace(store)
        await store.devices.create_device(
            device_id="dev-1",
            owner_id="owner-a",
            status="approved",
            bound_companion_id=workspace.companion.companion_id,
            interaction_mode="voice",
        )

        ctx = await ResolveOrchestrator(data_store=store).resolve_device("dev-1")

        assert ctx.owner_id == "owner-a"
        assert ctx.companion_id == workspace.companion.companion_id
        assert ctx.memory_realm_id == workspace.memory_realm.realm_id
        assert ctx.genome_id == workspace.persona_genome.genome_id
        assert ctx.device_id == "dev-1"
        assert ctx.interaction_mode == "voice"
    finally:
        await store.close()


async def test_resolve_device_rejects_unregistered_device(tmp_path) -> None:
    store = await _store(tmp_path)
    try:
        with pytest.raises(ResolveDeviceNotBound):
            await ResolveOrchestrator(data_store=store).resolve_device("missing")
    finally:
        await store.close()


async def test_resolve_device_rejects_unbound_device(tmp_path) -> None:
    store = await _store(tmp_path)
    try:
        await _workspace(store)
        await store.devices.create_device(
            device_id="dev-1",
            owner_id="owner-a",
            status="approved",
            bound_companion_id=None,
        )

        with pytest.raises(ResolveDeviceNotBound):
            await ResolveOrchestrator(data_store=store).resolve_device("dev-1")
    finally:
        await store.close()


async def test_resolve_device_rejects_disabled_device(tmp_path) -> None:
    store = await _store(tmp_path)
    try:
        workspace = await _workspace(store)
        await store.devices.create_device(
            device_id="dev-1",
            owner_id="owner-a",
            status="disabled",
            bound_companion_id=workspace.companion.companion_id,
        )

        with pytest.raises(ResolveDeviceUnavailable):
            await ResolveOrchestrator(data_store=store).resolve_device("dev-1")
    finally:
        await store.close()


async def test_resolve_device_rejects_cross_owner_companion_binding(tmp_path) -> None:
    store = await _store(tmp_path)
    try:
        await _workspace(store, owner_id="owner-a")
        other = await _workspace(store, owner_id="owner-b")
        await store.devices.create_device(
            device_id="dev-1",
            owner_id="owner-a",
            status="approved",
            bound_companion_id=None,
        )
        async with store.session_factory() as session:
            await session.execute(
                update(DeviceRow)
                .where(DeviceRow.device_id == "dev-1")
                .values(bound_companion_id=other.companion.companion_id)
            )
            await session.commit()

        with pytest.raises(ResolveDeviceUnavailable):
            await ResolveOrchestrator(data_store=store).resolve_device("dev-1")
    finally:
        await store.close()
