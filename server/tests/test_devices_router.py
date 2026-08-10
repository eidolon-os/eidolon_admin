"""Tests for the /devices/fleet join. Guard the invariant that a device's
logical role and grouping come from its companion binding (persona via
bound_companion_id, guard via guard_bindings) — never from the board kind."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from eidolon_data import DataSettings, DataStore
from fastapi import FastAPI

from eidolon_admin_server.app.devices.router import router as devices_router


@pytest.fixture
async def data_store(tmp_path) -> AsyncIterator[DataStore]:
    store = DataStore.open(DataSettings(sqlite_path=str(tmp_path / "eidolon.sqlite3")))
    await store.init_schema()
    yield store
    await store.close()


@pytest.fixture
async def client(data_store: DataStore) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.state.data_store = data_store
    # No Hub / runtime KV wired: both device sources must degrade gracefully.
    app.state.hub_device_client = None
    app.state.nats_kv = None
    app.include_router(devices_router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


async def test_fleet_groups_devices_by_companion_binding(
    client: httpx.AsyncClient,
    data_store: DataStore,
) -> None:
    await data_store.owner_service.create_owner(
        owner_id="owner-fleet",
        display_name="Fleet Owner",
        actor_type="test",
    )
    workspace = await data_store.workspace_provisioning.provision_workspace(
        owner_id="owner-fleet",
        companion_display_name="Xiaoyi",
        actor_type="test",
    )
    # Persona body: grouped under its persona companion via bound_companion_id.
    await data_store.devices.create_device(
        device_id="persona-desk",
        owner_id="owner-fleet",
        name="Desk Body",
        kind="m5stack-core-s3",
        bound_companion_id=workspace.companion.companion_id,
    )
    # Guard sentinel: grouped under its guard companion via guard_bindings only.
    guard = await data_store.guard_bindings.ensure_guard_companion(
        owner_id="owner-fleet", companion_id="guard-fleet"
    )
    await data_store.devices.create_device(
        device_id="atk-sentinel",
        owner_id="owner-fleet",
        name="Hallway Cam",
        kind="atk-dnesp32s3",
        capabilities_json={"guard": True},
    )
    await data_store.guard_bindings.claim(
        owner_id="owner-fleet",
        device_id="atk-sentinel",
        guard_companion_id=guard.companion_id,
    )
    # Unbound body: claimed to the owner but bound to no companion.
    await data_store.devices.create_device(
        device_id="loose-body",
        owner_id="owner-fleet",
        name="Loose Body",
        kind="atk-dnesp32s3",
    )

    resp = await client.get("/devices/fleet?owner_id=owner-fleet")
    assert resp.status_code == 200
    body = resp.json()
    assert body["owner_id"] == "owner-fleet"

    groups = {g["companion_id"]: g for g in body["groups"]}
    persona_group = groups[workspace.companion.companion_id]
    guard_group = groups[guard.companion_id]

    persona_ids = {d["device_id"] for d in persona_group["devices"]}
    guard_ids = {d["device_id"] for d in guard_group["devices"]}
    assert persona_ids == {"persona-desk"}
    assert guard_ids == {"atk-sentinel"}

    persona_device = persona_group["devices"][0]
    guard_device = guard_group["devices"][0]
    assert persona_device["role_kind"] == "persona"
    assert guard_device["role_kind"] == "guard"
    # Guard role is read from the binding, not from the identical board kind.
    assert guard_device["kind"] == "atk-dnesp32s3"

    unbound_ids = {d["device_id"] for d in body["unbound"]}
    assert unbound_ids == {"loose-body"}
    assert body["unbound"][0]["role_kind"] == "unbound"

    # Source health and entity presence are orthogonal: an unavailable
    # blackboard is still reported in signals, but must not turn inventory-only
    # devices into a fictitious per-device "degraded" state.
    all_devices = [
        device
        for group in body["groups"]
        for device in group["devices"]
    ] + body["unbound"]
    assert {device["status"] for device in all_devices} == {"offline"}
    assert all(device["online"] is False for device in all_devices)
    assert all(
        device["signals"]["blackboard_health"] == "degraded"
        for device in all_devices
    )
