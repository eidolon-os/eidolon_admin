from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from eidolon_data import DataSettings, DataStore
from eidolon_sdk.biz.body import (
    OwnerDeviceBlackboardSnapshot,
    owner_device_blackboard_key,
)
from fastapi import FastAPI

from eidolon_admin_server.app.mission_control import service as mission_control_service
from eidolon_admin_server.app.mission_control import router as mission_control_router
from eidolon_admin_server.app.mission_control.router import (
    _event_in_owner_scope,
    _events_tail,
    _runtime_event_stream,
)


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
    # Role is read from the bound (persona) companion, not the board kind.
    assert body["devices"][0]["role"] == "对话身体"
    assert body["devices"][0]["role_kind"] == "persona"
    assert body["devices"][0]["status"] == "degraded"
    assert body["devices"][0]["capabilities"] == []
    assert body["runtime_blackboard"]["health"] == "degraded"
    assert body["runtime_blackboard"]["snapshot"] is None
    assert any(item["source"] == "hub" and not item["ok"] for item in body["source_status"])
    assert body["privacy_notice"].startswith("Default safe mode")


async def test_device_role_reads_from_bound_companion_not_board_kind(
    client: httpx.AsyncClient,
    data_store: DataStore,
) -> None:
    await data_store.owner_service.create_owner(
        owner_id="owner-role",
        display_name="Role Owner",
        actor_type="test",
    )
    workspace = await data_store.workspace_provisioning.provision_workspace(
        owner_id="owner-role",
        companion_display_name="Xiaoyi",
        actor_type="test",
    )
    # Persona body: role comes from the persona companion it is bound to.
    await data_store.devices.create_device(
        device_id="persona-desk",
        owner_id="owner-role",
        name="Desk Body",
        kind="m5stack-core-s3",
        bound_companion_id=workspace.companion.companion_id,
    )
    # Guard sentinel: guard-capable device bound to a guard companion only via
    # guard_bindings (never via bound_companion_id).
    guard = await data_store.guard_bindings.ensure_guard_companion(
        owner_id="owner-role", companion_id="guard-role"
    )
    await data_store.devices.create_device(
        device_id="atk-sentinel",
        owner_id="owner-role",
        name="Hallway Cam",
        kind="atk-dnesp32s3",
        capabilities_json={"guard": True},
    )
    await data_store.guard_bindings.claim(
        owner_id="owner-role",
        device_id="atk-sentinel",
        guard_companion_id=guard.companion_id,
    )

    resp = await client.get("/api/mission-control/snapshot?owner_id=owner-role")
    assert resp.status_code == 200
    devices = {row["device_id"]: row for row in resp.json()["devices"]}

    # Persona binding -> persona role; the board kind never leaks into the role.
    assert devices["persona-desk"]["role_kind"] == "persona"
    assert devices["persona-desk"]["role"] == "对话身体"
    assert devices["persona-desk"]["kind"] == "m5stack-core-s3"

    # Guard binding -> guard role, even though the device is not bound via
    # bound_companion_id and its board kind says nothing about "guard".
    assert devices["atk-sentinel"]["role_kind"] == "guard"
    assert devices["atk-sentinel"]["role"] == "守护哨兵"
    assert devices["atk-sentinel"]["kind"] == "atk-dnesp32s3"


class _FakeRuntimeKV:
    def __init__(self, raw: bytes | None = None, error: Exception | None = None) -> None:
        self.raw = raw
        self.error = error
        self.calls: list[tuple[str, str]] = []

    async def get_existing(self, bucket: str, key: str) -> bytes | None:
        self.calls.append((bucket, key))
        if self.error is not None:
            raise self.error
        return self.raw


def _runtime_snapshot_bytes(
    owner_id: str,
    *,
    ready: bool = True,
    hub_lease_delta: int = 60,
    device_lease_delta: int = 45,
) -> bytes:
    now = datetime.now(UTC)
    snapshot = OwnerDeviceBlackboardSnapshot.model_validate(
        {
            "schema_version": 2,
            "owner_id": owner_id,
            "epoch": "epoch-live-7",
            "revision": 19,
            "ready": ready,
            "hub_lease_expires_at": now + timedelta(seconds=hub_lease_delta),
            "updated_at": now,
            "devices": {
                "guard-online": {
                    "device_id": "guard-online",
                    "registration_id": "reg-current-3",
                    "provider_companion_id": "companion-guard",
                    "provider_companion_name": "Guard Companion",
                    "name": "ATK Guard",
                    "aliases": ["门卫", "guard"],
                    "visibility": "bound_companion",
                    "capabilities": [
                        {
                            "name": "device.roll_call",
                            "version": 3,
                            "description": "Play the current roll-call cue.",
                            "input_schema": {
                                "type": "object",
                                "properties": {},
                                "additionalProperties": False,
                            },
                            "result_schema": {
                                "type": "object",
                                "properties": {"played": {"type": "boolean"}},
                                "required": ["played"],
                                "additionalProperties": False,
                            },
                        }
                    ],
                    "manifest_revision": "sha256:manifest-current",
                    "status": "online",
                    "registered_at": now - timedelta(seconds=20),
                    "lease_expires_at": now + timedelta(seconds=device_lease_delta),
                    "last_seen_at": now,
                    "room_name": "device-guard-online-control",
                    "participant_sid": "PA_GUARD",
                    "presence_revision": "presence-9",
                }
            },
        }
    )
    return snapshot.to_bytes()


async def test_runtime_blackboard_reads_only_selected_owner_current_key() -> None:
    owner_id = "owner-blackboard-a"
    kv = _FakeRuntimeKV(_runtime_snapshot_bytes(owner_id))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(nats_kv=kv)))
    statuses: list[mission_control_service.SourceStatus] = []

    result = await mission_control_service._runtime_blackboard(  # noqa: SLF001
        request, owner_id, statuses
    )

    assert kv.calls == [
        ("EIDOLON_RUNTIME_DEVICES", owner_device_blackboard_key(owner_id))
    ]
    assert result.health == "healthy"
    assert result.available is True
    assert result.snapshot is not None
    assert result.snapshot.owner_id == owner_id
    device = result.snapshot.devices["guard-online"]
    assert device.registration_id == "reg-current-3"
    assert device.provider_companion_name == "Guard Companion"
    assert device.capabilities[0].model_dump(mode="json") == {
        "name": "device.roll_call",
        "version": 3,
        "description": "Play the current roll-call cue.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "result_schema": {
            "type": "object",
            "properties": {"played": {"type": "boolean"}},
            "required": ["played"],
            "additionalProperties": False,
        },
    }
    assert statuses[-1].source == "runtime.blackboard" and statuses[-1].ok


async def test_runtime_blackboard_rejects_cross_owner_snapshot() -> None:
    kv = _FakeRuntimeKV(_runtime_snapshot_bytes("owner-foreign"))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(nats_kv=kv)))
    statuses: list[mission_control_service.SourceStatus] = []

    result = await mission_control_service._runtime_blackboard(  # noqa: SLF001
        request, "owner-selected", statuses
    )

    assert result.health == "degraded"
    assert result.available is False
    assert result.snapshot is None
    assert "owner mismatch" in result.detail
    assert statuses[-1].ok is False


@pytest.mark.parametrize(
    ("ready", "hub_lease_delta", "detail"),
    [(False, 60, "not ready"), (True, -1, "lease expired")],
)
async def test_runtime_blackboard_retains_raw_degraded_snapshot_but_fails_closed(
    ready: bool,
    hub_lease_delta: int,
    detail: str,
) -> None:
    owner_id = "owner-degraded"
    kv = _FakeRuntimeKV(
        _runtime_snapshot_bytes(
            owner_id,
            ready=ready,
            hub_lease_delta=hub_lease_delta,
        )
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(nats_kv=kv)))
    statuses: list[mission_control_service.SourceStatus] = []
    blackboard = await mission_control_service._runtime_blackboard(  # noqa: SLF001
        request, owner_id, statuses
    )

    persistent = SimpleNamespace(
        device_id="guard-online",
        owner_id=owner_id,
        name="Persisted Guard",
        kind="atk_camera",
        status="active",
        bound_companion_id="companion-guard",
        capabilities_json={"camera.capture": True},
        approved_at=datetime.now(UTC),
        network_json={},
    )
    devices = mission_control_service._merge_devices(  # noqa: SLF001
        [persistent],
        [],
        runtime_blackboard=blackboard,
        owner_id=owner_id,
    )

    assert blackboard.snapshot is not None  # raw current fields remain inspectable
    assert blackboard.health == "degraded"
    assert blackboard.available is False
    assert detail in blackboard.detail
    assert devices[0].online is False
    assert devices[0].status == "degraded"
    assert devices[0].capabilities == []


async def test_runtime_merge_uses_online_contract_and_drops_foreign_hub_rows() -> None:
    owner_id = "owner-runtime"
    kv = _FakeRuntimeKV(_runtime_snapshot_bytes(owner_id))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(nats_kv=kv)))
    statuses: list[mission_control_service.SourceStatus] = []
    blackboard = await mission_control_service._runtime_blackboard(  # noqa: SLF001
        request, owner_id, statuses
    )
    own_hub = SimpleNamespace(
        device_id="guard-online",
        owner_id=None,
        approved=True,
        paired=True,
        missed_probes=0,
    )
    foreign_hub = SimpleNamespace(
        device_id="foreign-device",
        owner_id="owner-foreign",
        approved=True,
    )

    devices = mission_control_service._merge_devices(  # noqa: SLF001
        [],
        [own_hub, foreign_hub],
        runtime_blackboard=blackboard,
        owner_id=owner_id,
    )

    assert [device.device_id for device in devices] == ["guard-online"]
    assert devices[0].owner_id == owner_id
    assert devices[0].online is True
    assert devices[0].capabilities == ["device.roll_call"]
    assert devices[0].room_name == "device-guard-online-control"


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
    # Every observed voice turn yields safe spans; history is not tied to one playhead.
    assert body["trace_spans"], "observed voice turn should yield spans"
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


def test_mission_control_http_surface_is_read_only() -> None:
    assert mission_control_router.routes
    assert all(route.methods <= {"GET", "HEAD"} for route in mission_control_router.routes)


def test_owner_scoped_stream_rejects_cross_owner_and_unattributed_hub_events() -> None:
    own = mission_control_service.RuntimeEvent(
        event_id="own",
        ts=datetime.now(UTC),
        source="hub",
        type="device.observed",
        owner_id="owner-a",
        summary="own device",
    )
    other = own.model_copy(update={"event_id": "other", "owner_id": "owner-b"})
    global_event = own.model_copy(update={"event_id": "global", "owner_id": None})

    assert _event_in_owner_scope(own, "owner-a")
    assert not _event_in_owner_scope(other, "owner-a")
    assert not _event_in_owner_scope(global_event, "owner-a")
    assert _event_in_owner_scope(global_event, None)


async def test_live_enrichment_never_invents_owner_for_global_hub_frame(
    data_store: DataStore,
) -> None:
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(data_store=data_store)),
    )
    global_event = mission_control_service.RuntimeEvent(
        event_id="hub-probe",
        ts=datetime.now(UTC),
        source="hub",
        type="device.presence.probe_cycle",
        summary="global probe",
    )

    enriched = await mission_control_service.enrich_runtime_event(request, global_event)

    assert enriched.owner_id is None
    assert not _event_in_owner_scope(enriched, "owner-selected")


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


async def test_snapshot_projects_channel_only_rejected_turn(
    client: httpx.AsyncClient,
    data_store: DataStore,
) -> None:
    """A turn that never reached Agent must still exist in Mission Control."""

    await data_store.owner_service.create_owner(
        owner_id="owner-channel-turn", display_name="Voice Owner", actor_type="test"
    )
    await data_store.workspace_provisioning.provision_workspace(
        owner_id="owner-channel-turn",
        companion_display_name="Xiaoyi",
        actor_type="test",
    )
    await data_store.devices.create_device(
        device_id="box-channel-turn",
        owner_id="owner-channel-turn",
        name="ESP BOX 3",
        kind="esp_box_3",
        bound_companion_id="c_owner-channel-turn_default",
    )
    base_payload = {
        "channel_turn_id": "channel-turn-1",
        "room_name": "room-channel-turn",
        "device_id": "box-channel-turn",
    }
    await data_store.events.record_event(
        event_id="evt-channel-phase-1",
        event_type="channel.turn.phase_changed",
        owner_id="owner-channel-turn",
        companion_id="c_owner-channel-turn_default",
        subject_type="turn",
        subject_id="channel-turn-1",
        trace_id="channel-turn-1",
        payload_json={
            **base_payload,
            "phase": "user_speech_open",
            "previous_phase": "idle",
            "transition_seq": 1,
            "transition_event": "speech_started",
            "side_effect": "none",
            "elapsed_ms": 0,
        },
    )
    await data_store.events.record_event(
        event_id="evt-channel-phase-2",
        event_type="channel.turn.phase_changed",
        owner_id="owner-channel-turn",
        companion_id="c_owner-channel-turn_default",
        subject_type="turn",
        subject_id="channel-turn-1",
        trace_id="channel-turn-1",
        payload_json={
            **base_payload,
            "phase": "user_turn_rejected",
            "previous_phase": "user_speech_open",
            "transition_seq": 2,
            "transition_event": "voiceprint_gate_rejected",
            "side_effect": "none",
            "elapsed_ms": 210,
        },
    )
    await data_store.events.record_event(
        event_id="evt-channel-terminal",
        event_type="channel.turn.rejected",
        owner_id="owner-channel-turn",
        companion_id="c_owner-channel-turn_default",
        subject_type="turn",
        subject_id="channel-turn-1",
        trace_id="channel-turn-1",
        reason="voiceprint_commit_blocked",
        payload_json={
            **base_payload,
            "status": "rejected",
            "terminal_reason": "voiceprint_commit_blocked",
            "durations_ms": {"speech_stop_to_commit": None},
            "missing_milestones": ["turn_committed", "brain_first_delta", "first_audio"],
        },
    )

    resp = await client.get("/api/mission-control/snapshot?owner_id=owner-channel-turn")

    assert resp.status_code == 200
    body = resp.json()
    turn = next(item for item in body["recent_turns"] if item["turn_id"] == "channel-turn-1")
    assert turn["trace_id"] == "channel-turn-1"
    assert turn["channel_turn_id"] == "channel-turn-1"
    assert turn["agent_turn_id"] is None
    assert turn["status"] == "rejected"
    assert turn["phase"] == "user_turn_rejected"
    assert turn["outcome"] == "denied"
    assert turn["terminal_reason"] == "voiceprint_commit_blocked"
    assert turn["event_ids"] == [
        "evt-channel-phase-1",
        "evt-channel-phase-2",
        "evt-channel-terminal",
    ]
    assert [stage["key"] for stage in turn["stages"]] == ["speech", "commit"]


async def test_snapshot_merges_channel_and_agent_turns_by_trace_id(
    client: httpx.AsyncClient,
    data_store: DataStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One logical turn keeps Channel timing and Agent evidence without duplicates."""

    owner_id = "owner-trace-merge"
    companion_id = "c_owner-trace-merge_default"
    channel_turn_id = "channel-turn-merged"
    await data_store.owner_service.create_owner(
        owner_id=owner_id, display_name="Voice Owner", actor_type="test"
    )
    await data_store.workspace_provisioning.provision_workspace(
        owner_id=owner_id,
        companion_display_name="Xiaoyi",
        actor_type="test",
    )
    base_payload = {
        "channel_turn_id": channel_turn_id,
        "room_name": "room-trace-merge",
        "device_id": "box-trace-merge",
    }
    await data_store.events.record_event(
        event_id="evt-trace-speech",
        event_type="channel.turn.phase_changed",
        owner_id=owner_id,
        companion_id=companion_id,
        subject_type="turn",
        subject_id=channel_turn_id,
        trace_id=channel_turn_id,
        payload_json={
            **base_payload,
            "phase": "user_speech_open",
            "previous_phase": "idle",
            "transition_seq": 1,
            "side_effect": "none",
            "elapsed_ms": 0,
        },
    )
    await data_store.events.record_event(
        event_id="evt-trace-brain",
        event_type="channel.turn.milestone",
        owner_id=owner_id,
        companion_id=companion_id,
        subject_type="turn",
        subject_id=channel_turn_id,
        trace_id=channel_turn_id,
        payload_json={
            **base_payload,
            "milestone": "brain_first_delta",
            "milestone_seq": 1,
            "elapsed_ms": 180,
            "brain_turn_id": "agent-turn-merged",
            "conversation_id": "conversation-merged",
        },
    )
    await data_store.events.record_event(
        event_id="evt-trace-complete",
        event_type="channel.turn.completed",
        owner_id=owner_id,
        companion_id=companion_id,
        subject_type="turn",
        subject_id=channel_turn_id,
        trace_id=channel_turn_id,
        payload_json={
            **base_payload,
            "status": "completed",
            "terminal_reason": "agent_playback_done",
            "durations_ms": {},
            "missing_milestones": [],
        },
    )

    async def fake_agent_turns(*_args, **_kwargs):
        return [
            {
                "turn_id": "agent-turn-merged",
                "trace_id": channel_turn_id,
                "conversation_id": "conversation-merged",
                "owner_id": owner_id,
                "companion_id": companion_id,
                "device_id": "box-trace-merge",
                "status": "completed",
                "started_at": datetime.now(UTC).isoformat(),
                "observability_summary": {
                    "memory": {"attempted": True, "hit_count": 3},
                    "tools": {"count": 1, "names": ["weather"]},
                    "memory_write": {"fanout_allowed": True, "disposition": "write"},
                },
            }
        ]

    monkeypatch.setattr(mission_control_service, "_agent_turns", fake_agent_turns)

    resp = await client.get(f"/api/mission-control/snapshot?owner_id={owner_id}")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["recent_turns"]) == 1
    turn = body["recent_turns"][0]
    assert turn["turn_id"] == channel_turn_id
    assert turn["channel_turn_id"] == channel_turn_id
    assert turn["agent_turn_id"] == "agent-turn-merged"
    assert turn["conversation_id"] == "conversation-merged"
    assert turn["memory_hits"] == 3
    assert turn["tool_names"] == ["weather"]
    assert [stage["key"] for stage in turn["stages"]] == [
        "speech",
        "response",
        "memory_recall",
        "tools",
        "memory_write",
    ]
    assert not any(activity["status"] == "running" for activity in body["activities"])
    assert {span["turn_id"] for span in body["trace_spans"]} == {channel_turn_id}
    agent_event = next(event for event in body["recent_events"] if event["type"] == "agent.turn.observed")
    assert agent_event["trace_id"] == channel_turn_id


async def test_snapshot_reconciles_unclosed_turn_at_authoritative_session_end(
    client: httpx.AsyncClient,
    data_store: DataStore,
) -> None:
    owner_id = "owner-orphan-turn"
    companion_id = "c_owner-orphan-turn_default"
    turn_id = "channel-turn-orphan"
    room_name = "room-orphan-turn"
    await data_store.owner_service.create_owner(
        owner_id=owner_id,
        display_name="Orphan Owner",
        actor_type="test",
    )
    await data_store.workspace_provisioning.provision_workspace(
        owner_id=owner_id,
        companion_display_name="Xiaoyi",
        actor_type="test",
    )
    await data_store.events.record_event(
        event_id="evt-orphan-committed",
        event_type="channel.turn.phase_changed",
        owner_id=owner_id,
        companion_id=companion_id,
        subject_type="turn",
        subject_id=turn_id,
        trace_id=turn_id,
        payload_json={
            "channel_turn_id": turn_id,
            "room_name": room_name,
            "device_id": "box-orphan",
            "phase": "user_turn_committed",
            "previous_phase": "user_turn_pending",
            "transition_seq": 1,
            "transition_event": "framework_completed_turn",
            "side_effect": "irreversible",
            "elapsed_ms": 100,
        },
    )
    await data_store.events.record_event(
        event_id="evt-orphan-first-audio",
        event_type="channel.turn.milestone",
        owner_id=owner_id,
        companion_id=companion_id,
        subject_type="turn",
        subject_id=turn_id,
        trace_id=turn_id,
        payload_json={
            "channel_turn_id": turn_id,
            "room_name": room_name,
            "device_id": "box-orphan",
            "milestone": "first_audio",
            "milestone_seq": 1,
            "elapsed_ms": 200,
        },
    )
    await data_store.events.record_event(
        event_id="evt-orphan-session-end",
        event_type="channel.session.ended",
        owner_id=owner_id,
        companion_id=companion_id,
        subject_type="device",
        subject_id="box-orphan",
        reason="idle_normal_end",
        payload_json={
            "room_name": room_name,
            "device_id": "box-orphan",
        },
    )

    resp = await client.get(f"/api/mission-control/snapshot?owner_id={owner_id}")

    assert resp.status_code == 200
    body = resp.json()
    turn = next(item for item in body["recent_turns"] if item["turn_id"] == turn_id)
    assert turn["status"] == "orphaned"
    assert turn["outcome"] == "failure"
    assert turn["terminal_reason"] == "session_ended_without_turn_terminal"
    assert turn["missing_milestones"] == ["terminal_event"]
    assert turn["event_ids"][-1] == "evt-orphan-session-end"
    assert turn["stages"][-1]["status"] == "degraded"
    assert not any(activity["status"] == "running" for activity in body["activities"])


def test_activity_projection_keeps_concurrent_companions_independent() -> None:
    turn_a = mission_control_service.RuntimeTurn(
        turn_id="turn-a",
        conversation_id="conv-a",
        owner_id="owner-a",
        companion_id="companion-a",
        device_id="box-a",
        status="running",
        outcome="deferred",
        started_at=datetime(2026, 7, 16, 12, 0, tzinfo=UTC),
        stages=[{"key": "brain", "label": "A 思考", "status": "running"}],
    )
    turn_b = mission_control_service.RuntimeTurn(
        turn_id="turn-b",
        conversation_id="conv-b",
        owner_id="owner-a",
        companion_id="companion-b",
        device_id="box-b",
        status="speaking",
        outcome="deferred",
        started_at=datetime(2026, 7, 16, 12, 0, 1, tzinfo=UTC),
        stages=[{"key": "playback", "label": "B 播放", "status": "running"}],
    )

    activities = mission_control_service._project_runtime_activities(
        [turn_a, turn_b], [], []
    )

    active = {item.companion_id: item for item in activities}
    assert active["companion-a"].current_hop_id.endswith(":brain:0")
    assert active["companion-b"].current_hop_id.endswith(":playback:0")
    assert active["companion-a"].route[-1].node_id == "agent"
    assert active["companion-b"].route[-1].node_id == "box-b"


def test_device_binding_enriches_guard_event_and_route() -> None:
    device = mission_control_service.RuntimeDevice(
        device_id="guard-device",
        owner_id="owner-guard",
        companion_id="guard-companion",
    )
    event = mission_control_service.RuntimeEvent(
        event_id="guard-event",
        ts=datetime(2026, 7, 16, 12, 0, tzinfo=UTC),
        source="hub",
        type="guard.presence.observed",
        device_id="guard-device",
        summary="Guard observed presence",
    )

    enriched = mission_control_service._enrich_event_scope([event], [device])[0]
    activity = mission_control_service._event_activity(enriched)

    assert enriched.companion_id == "guard-companion"
    assert activity is not None
    assert activity.kind == "guard_event"
    assert [hop.node_id for hop in activity.route] == [
        "guard-device",
        "hub",
        "guard-companion",
    ]


def test_guard_activity_drives_observer_state_without_voice_turn() -> None:
    guard = mission_control_service.RuntimeActivity(
        activity_id="guard:running",
        kind="guard_event",
        owner_id="owner-guard",
        companion_id="guard-companion",
        status="running",
        outcome="deferred",
        summary="Guard observing",
    )

    experience = mission_control_service._experience(
        owner=SimpleNamespace(owner_id="owner-guard", display_name="Guard Owner"),
        companion=None,
        devices=[],
        services=[],
        activities=[guard],
        primary_voice_turn=None,
        memory=mission_control_service.RuntimeMemory(),
        jobs=[],
        recent_events=[],
        source_status=[],
    )

    assert experience.system_state == "active"
    assert experience.headline == "Guard Owner 有 1 条运行活动"
    assert "不依赖语音轮次" in experience.subheadline
