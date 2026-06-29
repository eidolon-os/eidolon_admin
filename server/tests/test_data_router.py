from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import AsyncIterator

import httpx
import pytest
from eidolon_data import DataSettings, DataStore
from fastapi import FastAPI

from eidolon_admin_server.app.data import router as data_router


def _runtime_device(
    device_id: str,
    *,
    name: str = "ESP Device",
    status: str = "online",
    approved: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        device_id=device_id,
        name=name,
        kind="esp32",
        enabled=True,
        approved=approved,
        approved_at=datetime.now(timezone.utc) if approved else None,
        last_seen=datetime.now(timezone.utc),
        status=status,
        room_name=f"{device_id}-control",
        missed_probes=0,
    )


class FakeHubDeviceClient:
    def __init__(self) -> None:
        self.devices = {
            "esp-near": _runtime_device("esp-near", name="Nearby ESP", approved=True),
            "esp-pending": _runtime_device("esp-pending", name="Pending ESP"),
            "esp-owned": _runtime_device("esp-owned", name="Owned ESP"),
            "esp-ghost": _runtime_device("esp-ghost", name="Ghost ESP", status="offline", approved=True),
        }
        self.approved: list[str] = []

    async def list_devices(self) -> list[SimpleNamespace]:
        return list(self.devices.values())

    async def get_device(self, device_id: str) -> SimpleNamespace:
        return self.devices[device_id]

    async def identify_device(self, device_id: str) -> dict:
        return {
            "command_id": "cmd-identify",
            "device_id": device_id,
            "op": "device.identify",
            "status": "sent",
        }

    async def refresh_device_config(self, device_id: str) -> None:
        return None


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
    app.include_router(data_router, prefix="/api")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


async def test_owner_scoped_data_overview_and_lists(
    client: httpx.AsyncClient,
    data_store: DataStore,
) -> None:
    created = await client.post(
        "/api/owners",
        json={"owner_id": "owner-a", "display_name": "Owner A", "kind": "person"},
    )
    assert created.status_code == 201
    assert created.json()["status"] == "active"

    duplicate = await client.post(
        "/api/owners",
        json={"owner_id": "owner-a", "display_name": "Owner A"},
    )
    assert duplicate.status_code == 409

    empty_overview = await client.get("/api/owners/owner-a/workspace")
    assert empty_overview.status_code == 200
    assert empty_overview.json()["initialized"] is False

    initialized = await client.post(
        "/api/owners/owner-a/workspace/initialize",
        json={
            "companion_display_name": "Xiaoyi",
            "genome_json": {"tone": "warm"},
            "prompt_markdown": "# Xiaoyi\n\n## Style\n\n- Warm.\n",
            "memory_policy_json": {"scope": "owner"},
        },
    )
    assert initialized.status_code == 200
    assert initialized.json()["companion"]["companion_id"] == "c_owner-a_default"
    assert initialized.json()["persona_genome"]["genome_id"] == "g_owner-a_default_v1"
    assert initialized.json()["persona_genome"]["status"] == "committed"
    assert initialized.json()["persona_genome"]["prompt_markdown"].startswith("# Xiaoyi")
    assert initialized.json()["memory_realm"]["realm_id"] == "r_owner-a_default"

    await data_store.devices.create_device(
        device_id="device-a",
        owner_id="owner-a",
        name="Desk Body",
        kind="voice_body",
        bound_companion_id="c_owner-a_default",
    )
    await data_store.conversations.create_conversation(
        conversation_id="conversation-a",
        owner_id="owner-a",
        companion_id="c_owner-a_default",
        title="Morning",
    )
    await data_store.jobs.create(
        job_id="job-a",
        owner_id="owner-a",
        companion_id="c_owner-a_default",
        provider="mementos",
        kind="daily_report",
    )

    overview = await client.get("/api/owners/owner-a/workspace")
    assert overview.status_code == 200
    body = overview.json()
    assert body["owner"]["owner_id"] == "owner-a"
    assert body["initialized"] is True
    assert body["counts"] == {
        "companions": 1,
        "persona_genomes": 1,
        "devices": 1,
        "conversations": 1,
        "memory_realms": 1,
        "jobs": 1,
        "events": 5,
    }
    assert body["companions"][0]["companion_id"] == "c_owner-a_default"
    assert body["devices"][0]["device_id"] == "device-a"
    assert body["conversations"][0]["conversation_id"] == "conversation-a"
    assert body["memory_realms"][0]["realm_id"] == "r_owner-a_default"
    assert body["jobs"][0]["job_id"] == "job-a"
    assert {event["event_type"] for event in body["events"]} >= {"owner.created", "companion.workspace.initialized"}

    genomes = await client.get("/api/owners/owner-a/persona-genomes")
    assert genomes.status_code == 200
    assert genomes.json()["persona_genomes"][0]["genome_id"] == "g_owner-a_default_v1"
    assert genomes.json()["persona_genomes"][0]["prompt_markdown"].startswith("# Xiaoyi")

    missing = await client.get("/api/owners/missing/workspace")
    assert missing.status_code == 404


async def test_owner_nearby_devices_identify_and_add_to_owner(
    data_store: DataStore,
) -> None:
    app = FastAPI()
    app.state.data_store = data_store
    fake_hub = FakeHubDeviceClient()
    app.state.hub_device_client = fake_hub
    app.include_router(data_router, prefix="/api")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        await client.post("/api/owners", json={"owner_id": "owner-devices"})
        await client.post(
            "/api/owners/owner-devices/workspace/initialize",
            json={"companion_display_name": "Companion"},
        )
        await data_store.devices.create_device(
            device_id="esp-owned",
            owner_id="owner-devices",
            name="Owned ESP",
            kind="esp32",
        )

        nearby = await client.get("/api/owners/owner-devices/nearby-devices")
        assert nearby.status_code == 200
        assert nearby.json()["hub_available"] is True
        assert [row["device_id"] for row in nearby.json()["devices"]] == ["esp-near"]
        assert nearby.json()["devices"][0]["approved"] is True

        identify = await client.post("/api/owners/owner-devices/nearby-devices/esp-near/identify")
        assert identify.status_code == 200
        assert identify.json()["op"] == "device.identify"

        pending_identify = await client.post("/api/owners/owner-devices/nearby-devices/esp-pending/identify")
        assert pending_identify.status_code == 412

        pending_claim = await client.post(
            "/api/owners/owner-devices/nearby-devices/esp-pending/claim",
            json={"name": "Pending ESP"},
        )
        assert pending_claim.status_code == 412

        added = await client.post(
            "/api/owners/owner-devices/nearby-devices/esp-near/claim",
            json={
                "name": "Nearby ESP",
                "companion_id": "c_owner-devices_default",
                "interaction_mode": "voice",
            },
        )
        assert added.status_code == 200
        assert added.json()["owner_id"] == "owner-devices"
        assert added.json()["status"] == "active"
        assert added.json()["bound_companion_id"] == "c_owner-devices_default"
        assert added.json()["metadata_json"]["source"] == "hub_runtime"
        assert added.json()["metadata_json"]["hub_approved"] is True
        assert fake_hub.approved == []

        updated = await client.patch(
            "/api/owners/owner-devices/devices/esp-near",
            json={"name": "box-3", "metadata_json": {"aliases": ["box-3", "客厅音箱"]}},
        )
        assert updated.status_code == 200
        assert updated.json()["name"] == "box-3"
        assert updated.json()["metadata_json"]["aliases"] == ["box-3", "客厅音箱"]
        assert updated.json()["metadata_json"]["source"] == "hub_runtime"

        owner_identify = await client.post("/api/owners/owner-devices/devices/esp-near/identify")
        assert owner_identify.status_code == 200
        assert owner_identify.json()["op"] == "device.identify"

        fake_hub.devices["esp-second"] = _runtime_device("esp-second", name="Second ESP", approved=True)
        duplicate_binding = await client.post(
            "/api/owners/owner-devices/nearby-devices/esp-second/claim",
            json={
                "name": "Second ESP",
                "companion_id": "c_owner-devices_default",
                "interaction_mode": "voice",
            },
        )
        assert duplicate_binding.status_code == 409

        empty_nearby = await client.get("/api/owners/owner-devices/nearby-devices")
        assert empty_nearby.status_code == 200
        assert [row["device_id"] for row in empty_nearby.json()["devices"]] == ["esp-second"]

        unbound = await client.post("/api/owners/owner-devices/devices/esp-near/bind-companion")
        assert unbound.status_code == 200
        assert unbound.json()["bound_companion_id"] is None

        released = await client.post("/api/owners/owner-devices/devices/esp-near/release")
        assert released.status_code == 200
        assert released.json()["owner_id"] is None
        assert released.json()["bound_companion_id"] is None

        events = await data_store.events.list_for_subject(
            subject_type="device",
            subject_id="esp-near",
        )
        assert [event.event_type for event in events] == [
            "device.claimed",
            "device.bound_companion",
            "device.updated",
            "device.bound_companion",
            "device.released",
        ]


async def test_data_router_returns_503_without_datastore() -> None:
    app = FastAPI()
    app.state.data_store = None
    app.include_router(data_router, prefix="/api")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        response = await ac.get("/api/owners")
    assert response.status_code == 503
