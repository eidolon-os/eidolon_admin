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
from eidolon_admin_server.app.mission_control.router import _events_tail, _runtime_event_stream


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
    # active turn yields structured spans, and they never carry text fields
    assert body["trace_spans"], "active turn should yield spans"
    for span in body["trace_spans"]:
        assert {"span_id", "turn_id", "name", "kind"} <= set(span)
        assert not any(k in span for k in ("content", "text", "transcript", "prompt"))


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
    assert '"event_origin"' in first and '"live"' in first


async def test_events_tail_streams_new_db_events(data_store: DataStore) -> None:
    """P2d — the cursor tail turns owner-scoped audit rows into live RuntimeEvents."""
    await data_store.owner_service.create_owner(
        owner_id="owner-tail-x", display_name="Tail", actor_type="test"
    )
    await data_store.events.record_event(
        event_type="owner.updated",
        owner_id="owner-tail-x",
        subject_type="owner",
        subject_id="owner-tail-x",
        actor_type="admin",
    )
    await data_store.events.record_event(
        event_type="device.revoked",
        owner_id="owner-tail-x",
        subject_type="device",
        subject_id="d1",
        actor_type="admin",
    )

    calls = {"n": 0}

    async def _is_disconnected() -> bool:
        calls["n"] += 1
        return calls["n"] > 1  # False on the first loop check, True after one poll

    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(data_store=data_store)),
        is_disconnected=_is_disconnected,
    )
    since = datetime(2020, 1, 1, tzinfo=UTC)
    events = [
        event
        async for event in _events_tail(request, "owner-tail-x", interval=0.0, since=since)  # type: ignore[arg-type]
    ]

    by_type = {event.type: event for event in events}
    assert {"owner.created", "owner.updated", "device.revoked"} <= set(by_type)
    # classification columns flow onto the live event (not string-guessed).
    assert by_type["device.revoked"].source == "admin"
    assert by_type["device.revoked"].severity == "warn"
    assert by_type["device.revoked"].device_id == "d1"


async def test_events_tail_noop_without_owner_or_store() -> None:
    class _Req:
        async def is_disconnected(self) -> bool:
            return False

    # No owner → immediate return (no infinite loop, no store access).
    assert [e async for e in _events_tail(_Req(), None, interval=0.0)] == []  # type: ignore[arg-type]


async def test_events_stream_replay_is_replay_origin() -> None:
    class FakeRequest:
        async def is_disconnected(self) -> bool:
            return True

    stream = _runtime_event_stream(FakeRequest(), None, "replay")  # type: ignore[arg-type]
    try:
        first = (await anext(stream)).decode("utf-8")
    finally:
        await stream.aclose()

    assert "mission_control.connected" in first
    assert '"event_origin"' in first and '"replay"' in first


async def test_snapshot_replay_sets_demo_mode(
    client: httpx.AsyncClient,
    data_store: DataStore,
) -> None:
    await data_store.owner_service.create_owner(
        owner_id="owner-r", display_name="R", actor_type="test"
    )
    resp = await client.get("/api/mission-control/snapshot?owner_id=owner-r&mode=replay")
    assert resp.status_code == 200
    assert resp.json()["demo_mode"] == "replay"


async def test_snapshot_exposes_contract_layer(
    client: httpx.AsyncClient,
    data_store: DataStore,
) -> None:
    await data_store.owner_service.create_owner(
        owner_id="owner-cl", display_name="CL", actor_type="test"
    )
    await data_store.workspace_provisioning.provision_workspace(
        owner_id="owner-cl", companion_display_name="Xiaoyi", actor_type="test"
    )
    await data_store.devices.create_device(
        device_id="cam-1",
        owner_id="owner-cl",
        name="ATK Camera",
        kind="atk_camera",
        bound_companion_id="c_owner-cl_default",
        capabilities_json={"camera.snapshot": True},
    )
    await data_store.events.append(
        event_id="ev-cam",
        owner_id="owner-cl",
        subject_type="device",
        subject_id="cam-1",
        event_type="self.camera.take_photo",
        payload_json={"image": "raw bytes should be redacted"},
    )

    resp = await client.get("/api/mission-control/snapshot?owner_id=owner-cl")
    assert resp.status_code == 200
    body = resp.json()

    # demo mode + provenance on every event
    assert body["demo_mode"] == "live"
    assert body["recent_events"], "expected at least one event"
    assert all(ev["event_origin"] == "polling" for ev in body["recent_events"])

    # three honest proof chains
    chains = {c["key"]: c for c in body["evidence_chains"]}
    assert set(chains) == {"cross_body_memory", "vision_permission", "coworker_task"}
    for chain in chains.values():
        assert 0 <= chain["confidence"] <= 100
        assert chain["status"] in {"pending", "partial", "proven"}
    assert chains["vision_permission"]["confidence"] > 0  # camera capability + grant

    # permission ledger captured the camera call, no raw image leaked
    cam = next(i for i in body["permission_ledger"] if i["kind"] == "camera.take_photo")
    assert cam["privacy_level"] == "sensitive"
    assert cam["raw_retention"] == "not_stored"
    cam_event = next(ev for ev in body["recent_events"] if ev["event_id"] == "ev-cam")
    assert "[redacted:" in cam_event["payload"]["image"]
