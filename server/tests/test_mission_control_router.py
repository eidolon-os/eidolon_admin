from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from eidolon_data import DataSettings, DataStore
from fastapi import FastAPI

from eidolon_admin_server.app.mission_control import service as mission_control_service
from eidolon_admin_server.app.mission_control import router as mission_control_router
from eidolon_admin_server.app.mission_control.router import _runtime_event_stream


@pytest.fixture
async def data_store(tmp_path) -> AsyncIterator[DataStore]:
    store = DataStore.open(DataSettings(sqlite_path=str(tmp_path / "eidolon.sqlite3")))
    await store.init_schema()
    yield store
    await store.close()


class FakeRegistry:
    services = [
        SimpleNamespace(
            id="agent",
            name="Eidolon Agent",
            base_url="",
            upstream_prefix="",
            health="",
        ),
        SimpleNamespace(
            id="hub",
            name="Eidolon Hub",
            base_url="",
            upstream_prefix="",
            health="",
        ),
    ]

    def get(self, service_id: str):
        for service in self.services:
            if service.id == service_id:
                return service
        return None


@pytest.fixture
async def client(data_store: DataStore) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.state.data_store = data_store
    app.state.registry = FakeRegistry()
    app.state.http_client = httpx.AsyncClient()
    app.state.hub_device_client = None
    app.include_router(mission_control_router, prefix="/api")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac
    await app.state.http_client.aclose()


async def test_snapshot_degrades_without_runtime_sources(
    client: httpx.AsyncClient,
    data_store: DataStore,
) -> None:
    await data_store.owner_service.create_owner(
        owner_id="owner-mc",
        display_name="Mission Owner",
        actor_type="test",
    )
    await data_store.workspace_provisioning.provision_workspace(
        owner_id="owner-mc",
        companion_display_name="Xiaoyi",
        actor_type="test",
    )
    await data_store.devices.create_device(
        device_id="esp-box-3-desk",
        owner_id="owner-mc",
        name="ESP-BOX-3 Desk",
        kind="esp_box_3",
        bound_companion_id="c_owner-mc_default",
        capabilities_json={"voice": True, "display": True},
    )

    resp = await client.get("/api/mission-control/snapshot?owner_id=owner-mc")
    assert resp.status_code == 200
    body = resp.json()
    assert body["owner"]["owner_id"] == "owner-mc"
    assert body["companion"]["display_name"] == "Xiaoyi"
    assert body["companions"][0]["display_name"] == "Xiaoyi"
    assert body["devices"][0]["role"] == "Room Voice / Sensor Dock"
    assert body["devices"][0]["capabilities"] == ["display", "sensor", "speaker", "voice"]
    assert any(item["source"] == "hub" and not item["ok"] for item in body["source_status"])
    assert body["privacy_notice"].startswith("Default safe mode")


async def test_snapshot_sorts_mixed_timezone_events(
    client: httpx.AsyncClient,
    data_store: DataStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await data_store.owner_service.create_owner(
        owner_id="owner-time",
        display_name="Mission Owner",
        actor_type="test",
    )
    await data_store.workspace_provisioning.provision_workspace(
        owner_id="owner-time",
        companion_display_name="Xiaoyi",
        actor_type="test",
    )
    await data_store.events.append(
        event_id="event-naive-time",
        owner_id="owner-time",
        subject_type="device",
        subject_id="device-1",
        event_type="device.observed",
        payload_json={"content": "private raw transcript should redact"},
    )

    async def fake_agent_turns(*_args, **_kwargs):
        return [
            {
                "turn_id": "turn-aware-time",
                "conversation_id": "conv-time",
                "owner_id": "owner-time",
                "companion_id": "c_owner-time_default",
                "status": "ok",
                "started_at": datetime.now(UTC).isoformat(),
                "observability_summary": {"memory": {"hit_count": 2}},
            }
        ]

    monkeypatch.setattr(mission_control_service, "_agent_turns", fake_agent_turns)

    resp = await client.get("/api/mission-control/snapshot?owner_id=owner-time")

    assert resp.status_code == 200
    body = resp.json()
    assert body["recent_events"][0]["source"] == "agent"
    data_event = next(item for item in body["recent_events"] if item["event_id"] == "event-naive-time")
    assert "[redacted:" in data_event["payload"]["content"]


async def test_events_stream_emits_startup_frame() -> None:
    class FakeRequest:
        async def is_disconnected(self) -> bool:
            return True

    stream = _runtime_event_stream(FakeRequest(), "owner-time")  # type: ignore[arg-type]
    try:
        first = (await anext(stream)).decode("utf-8")
    finally:
        await stream.aclose()

    assert first.startswith("event: runtime_event\n")
    assert "mission_control.connected" in first
