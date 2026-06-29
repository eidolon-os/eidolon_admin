from __future__ import annotations

import pytest

from eidolon_admin_server.app.resolve.orchestrator import (
    ResolveOrchestrator,
    ResolvePrecondition,
)
from eidolon_data import DataSettings, DataStore

pytestmark = pytest.mark.asyncio


async def _store(tmp_path) -> DataStore:
    store = DataStore.open(DataSettings(sqlite_path=str(tmp_path / "eidolon.sqlite3")))
    await store.init_schema()
    return store


async def _workspace(store: DataStore, *, owner_id: str = "owner-a"):
    await store.owner_service.create_owner(owner_id=owner_id, display_name=owner_id)
    return await store.workspace_provisioning.provision_workspace(owner_id=owner_id)


async def test_resolve_owner_returns_default_runtime_identity(tmp_path) -> None:
    store = await _store(tmp_path)
    try:
        workspace = await _workspace(store)

        ctx = await ResolveOrchestrator(data_store=store).resolve_owner("owner-a")

        assert ctx.owner_id == "owner-a"
        assert ctx.companion_id == workspace.companion.companion_id
        assert ctx.memory_realm_id == workspace.memory_realm.realm_id
        assert ctx.genome_id == workspace.persona_genome.genome_id
        assert ctx.device_id is None
    finally:
        await store.close()


async def test_resolve_device_returns_device_bound_runtime_identity(tmp_path) -> None:
    store = await _store(tmp_path)
    try:
        workspace = await _workspace(store)
        await store.devices.create_device(
            device_id="dev-1",
            owner_id="owner-a",
            status="active",
            bound_companion_id=workspace.companion.companion_id,
            interaction_mode="voice",
        )

        ctx = await ResolveOrchestrator(data_store=store).resolve_device("dev-1")

        assert ctx.owner_id == "owner-a"
        assert ctx.companion_id == workspace.companion.companion_id
        assert ctx.device_id == "dev-1"
        assert ctx.interaction_mode == "voice"
    finally:
        await store.close()


async def test_resolve_owner_rejects_uninitialized_owner(tmp_path) -> None:
    store = await _store(tmp_path)
    try:
        await store.owner_service.create_owner(owner_id="owner-a", display_name="Owner A")

        with pytest.raises(ResolvePrecondition):
            await ResolveOrchestrator(data_store=store).resolve_owner("owner-a")
    finally:
        await store.close()
