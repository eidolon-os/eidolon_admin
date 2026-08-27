"""The Owner's runtime map: what it may claim, and what it must never claim.

Three properties are held here, and each one is a mistake this projection was
built to make impossible:

* **the shape is the SDK's, not this file's.** Validated against
  ``mission-control-snapshot.schema.json`` — the same document the phone's
  parser is pinned to by golden — so the two sides cannot drift apart while both
  passing their own tests;
* **a lane nobody could read carries no rows.** Composed rows for an unreadable
  lane are not observations; shipping them is how a failure becomes invisible;
* **every source decides a lane, and the registry is total.** A source added to
  the composition without saying which lanes it decides raises rather than
  quietly deciding nothing — the flat status list this replaced could carry a
  timeout that never reached a panel.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import jsonschema
import pytest
from referencing import Registry, Resource

from eidolon_admin_server.app.management.mission_control import (
    LANE_LIMITS,
    owner_runtime_projection,
)
from eidolon_admin_server.app.mission_control.lanes import (
    LANES,
    SOURCE_LANES,
    LaneLedger,
    UnregisteredSource,
)
from eidolon_admin_server.app.mission_control.schemas import (
    RuntimeActivity,
    RuntimeDevice,
    RuntimeEvent,
    RuntimeJob,
    RuntimeMemory,
    RuntimeRouteHop,
    RuntimeService,
    RuntimeSnapshot,
    RuntimeTurn,
)
from eidolon_admin_server.app.mission_control.service import RuntimeComposition
from eidolon_admin_server.audit import IndexedAuditEvent
from eidolon_sdk.biz.audit import AuditEnvelope

_NOW = datetime(2026, 8, 26, 12, 30, tzinfo=UTC)


def _contracts() -> Path:
    """Where the SDK's contracts live in this checkout.

    Resolved from disk rather than fetched: the schemas reference each other and
    the audit envelope by declared ``$id``, which is the point — the shared
    vocabularies are referenced, not copied — but a test that needs the network
    to check a contract fails for reasons that have nothing to do with the
    contract.
    """

    for parent in Path(__file__).resolve().parents:
        candidate = parent / "eidolon_sdk" / "contracts"
        if candidate.is_dir():
            return candidate
    pytest.skip("eidolon_sdk is not checked out beside eidolon_admin")


def _validator() -> jsonschema.Draft202012Validator:
    contracts = _contracts()
    paths = [
        contracts / "mission_control" / "v1" / "mission-control-snapshot.schema.json",
        contracts / "mission_control" / "v1" / "mission-control-event.schema.json",
        # Declared $id says v1; on disk the audit contract is unversioned.
        # Registered by its own $id either way, so the ref resolves.
        *contracts.glob("audit/**/envelope.schema.json"),
    ]
    resources = []
    for path in paths:
        if not path.exists():
            continue
        schema = json.loads(path.read_text(encoding="utf-8"))
        resources.append((schema["$id"], Resource.from_contents(schema)))
    snapshot = json.loads(paths[0].read_text(encoding="utf-8"))
    return jsonschema.Draft202012Validator(
        snapshot,
        registry=Registry().with_resources(resources),
        format_checker=jsonschema.FormatChecker(),
    )


def _snapshot(**overrides) -> RuntimeSnapshot:
    base = dict(
        generated_at=_NOW,
        devices=[
            RuntimeDevice(
                device_id="dev-living",
                name="客厅音箱",
                role="对话身体",
                role_kind="persona",
                kind="esp32-s3",
                status="online",
                online=True,
                approved=True,
                companion_id="companion-a",
                capabilities=["voice.duplex"],
                last_seen_at=_NOW,
                signals={"presence_source": "runtime_blackboard"},
            )
        ],
        services=[
            RuntimeService(
                service_id="hub",
                name="设备中枢",
                online=True,
                checked=True,
                latency_ms=12.0,
                detail="7 个身体注册在册",
            )
        ],
        activities=[
            RuntimeActivity(
                activity_id="activity-1",
                kind="voice_turn",
                owner_id="owner-1",
                companion_id="companion-a",
                turn_id="turn-1",
                origin_device_id="dev-living",
                target_device_ids=["dev-living"],
                status="running",
                outcome="deferred",
                summary="一次语音对话正在推进",
                current_hop_id="hop-agent",
                started_at=_NOW,
                updated_at=_NOW,
                route=[
                    RuntimeRouteHop(
                        hop_id="hop-agent",
                        # What the composition really emits for a thinking hop:
                        # the Agent is a service, and the node it runs on says so.
                        node_type="service",
                        node_id="agent",
                        label="推理",
                        stage="agent_turn",
                        status="running",
                        direction="internal",
                        ts=_NOW,
                        latency_ms=61,
                    )
                ],
            )
        ],
        recent_turns=[
            RuntimeTurn(
                turn_id="turn-1",
                conversation_id="conv-1",
                owner_id="owner-1",
                companion_id="companion-a",
                device_id="dev-living",
                status="running",
                trigger="voice",
                latency_ms=780,
                memory_hits=4,
                stages=[{"key": "input", "label": "输入", "status": "done", "latency_ms": 42}],
            )
        ],
        jobs=[
            RuntimeJob(
                job_id="job-1",
                owner_id="owner-1",
                companion_id="companion-a",
                provider="agent",
                kind="research",
                status="running",
                summary="在查一件事",
            )
        ],
        memory=RuntimeMemory(
            realms_total=2,
            active_realm_id="realm-a",
            runners_total=3,
            runners_online=3,
            last_recall_hits=4,
        ),
        recent_events=[
            RuntimeEvent(
                event_id="event-1",
                ts=_NOW,
                source="hub",
                type="hub.device.joined",
                severity="info",
                outcome="success",
                privacy="safe",
                event_origin="live",
                companion_id="companion-a",
                device_id="dev-living",
                summary="客厅音箱 加入语音房间",
            )
        ],
    )
    base.update(overrides)
    return RuntimeSnapshot(**base)


def _audit(seq: int = 41, **overrides) -> IndexedAuditEvent:
    fields = dict(
        event_id=f"event-{seq}",
        producer="hub",
        producer_seq=seq,
        # The audit stream is governance and receipts — what was decided and
        # what was done. The runtime chain is the activities lane, not this one.
        category="receipt",
        owner_id="owner-1",
        subject_type="device",
        subject_id="dev-living",
        action="hub.device.joined",
        outcome="success",
        severity="info",
        reason="客厅音箱 加入语音房间",
        trace_id=None,
        data_classification="safe",
        schema_version=1,
        payload={"companion_id": "companion-a"},
        occurred_at=_NOW,
    )
    fields.update(overrides)
    return IndexedAuditEvent(ingest_seq=seq, envelope=AuditEnvelope(**fields))


def _healthy_ledger() -> LaneLedger:
    ledger = LaneLedger()
    for source in (
        "runtime.blackboard",
        "host.services",
        "agent.turns",
        "data.conversations",
        "agent.long_tasks",
        "data.jobs",
        "data.memory",
        "memory.runners",
        "audit.index",
    ):
        ledger.record(source, ok=True)
    return ledger


def test_projection_satisfies_the_sdk_contract() -> None:
    payload = owner_runtime_projection(
        RuntimeComposition(snapshot=_snapshot(), ledger=_healthy_ledger()),
        audit_events=[_audit()],
    )
    _validator().validate(payload)
    assert payload["coverage"] == "owner-runtime"
    # Identity is the roster's and /context's. A second place naming Companions
    # is a second place that can be wrong about them.
    assert "owner" not in payload
    assert "companions" not in payload
    assert "default_companion_id" not in payload
    # Operator-only material must not cross the plane.
    for leaked in ("runtime_blackboard", "trace_spans", "evidence_chains", "permission_ledger"):
        assert leaked not in payload


def test_a_lane_nobody_could_read_carries_no_rows() -> None:
    # The devices lane's only source in this process, gone.
    ledger = LaneLedger()
    for source in ("host.services", "agent.turns", "agent.long_tasks", "audit.index"):
        ledger.record(source, ok=True)
    ledger.record("runtime.blackboard", ok=False, detail="NATS KV 不可用")

    payload = owner_runtime_projection(
        RuntimeComposition(snapshot=_snapshot(), ledger=ledger),
        audit_events=[_audit()],
    )
    _validator().validate(payload)

    devices = payload["devices"]
    assert devices["state"] == "unavailable"
    assert "NATS KV" in devices["detail"]
    # Composed rows existed. They were not observed on this reading, so they do
    # not ship: an unreadable lane with rows in it is a failure nobody can see.
    assert devices["items"] == []
    # And only that lane. The rest of the map is still worth drawing.
    assert payload["services"]["state"] == "ok"
    assert payload["turns"]["state"] == "ok"
    assert payload["services"]["items"]


def test_one_source_of_two_leaves_a_lane_partly_known() -> None:
    """Degraded, not unavailable — and not ok either.

    Held on the turns lane, which really does have two sources in this process.
    Calling a partial answer unavailable throws away what was read; calling it
    ok hides what was not.
    """

    ledger = _healthy_ledger()
    ledger.record("data.conversations", ok=False, detail="Data 不发布对话历史")

    payload = owner_runtime_projection(
        RuntimeComposition(snapshot=_snapshot(), ledger=ledger),
        audit_events=[_audit()],
    )
    _validator().validate(payload)
    assert payload["turns"]["state"] == "degraded"
    assert "Data 不发布对话历史" in payload["turns"]["detail"]
    # And it still carries what the Agent answered.
    assert payload["turns"]["items"]


def test_a_retired_capability_decides_no_lane() -> None:
    """Said out loud, and costing nothing.

    Hub's management client no longer publishes an owner device page or an event
    feed. The composition records that rather than asking — and because those
    sources decide no lane, a lane does not go dark over a capability that moved.
    """

    ledger = _healthy_ledger()
    ledger.record("hub.device_page", ok=False, detail="不再发布")
    ledger.record("hub.event_feed", ok=False, detail="不再发布")
    ledger.record("data.guard_bindings", ok=False, detail="Guard 运行时还不存在")

    payload = owner_runtime_projection(
        RuntimeComposition(snapshot=_snapshot(), ledger=ledger),
        audit_events=[_audit()],
    )
    assert payload["devices"]["state"] == "ok"
    assert payload["events"]["state"] == "ok"


def test_one_source_reaches_every_lane_it_decides() -> None:
    """The Agent's turn feed is where both the turns and the activity chain come
    from. Losing it must reach both — otherwise one of them looks quiet.

    Both land on ``degraded`` rather than ``unavailable`` here because each lane
    has another source that answered. That is the honest reading: partly known,
    with the reason for the rest attached. On the Host as it is today the other
    sources are unexposed, so the same loss makes both lanes unavailable — which
    is what ``test_a_lane_nobody_could_read_carries_no_rows`` holds.
    """

    ledger = _healthy_ledger()
    ledger.record("agent.turns", ok=False, detail="Agent 没有回应")

    payload = owner_runtime_projection(
        RuntimeComposition(snapshot=_snapshot(), ledger=ledger),
        audit_events=[_audit()],
    )
    for lane in ("turns", "activities"):
        assert payload[lane]["state"] == "degraded", lane
        assert "Agent 没有回应" in payload[lane]["detail"], lane
    # And a lane it does not decide is untouched.
    assert payload["devices"]["state"] == "ok"


def test_a_lane_no_source_bears_on_is_unknown_not_empty() -> None:
    # An empty ledger read nothing. Every lane is then unknown — not an Owner
    # with a quiet house.
    payload = owner_runtime_projection(
        RuntimeComposition(snapshot=_snapshot(), ledger=LaneLedger()),
    )
    _validator().validate(payload)
    for lane in LANES:
        assert payload[lane]["state"] == "unavailable", lane
        assert payload[lane]["detail"]


def test_a_cut_short_lane_says_so() -> None:
    limit = LANE_LIMITS["devices"]
    snapshot = _snapshot(
        devices=[
            RuntimeDevice(
                device_id=f"dev-{index}",
                name=f"身体 {index}",
                role="对话身体",
                role_kind="persona",
                kind="esp32-s3",
                status="online",
                online=True,
                approved=True,
                capabilities=[],
                signals={"presence_source": "runtime_blackboard"},
            )
            for index in range(limit + 5)
        ]
    )
    payload = owner_runtime_projection(
        RuntimeComposition(snapshot=snapshot, ledger=_healthy_ledger()),
        audit_events=[_audit()],
    )
    _validator().validate(payload)
    assert payload["devices"]["truncated"] is True
    assert len(payload["devices"]["items"]) == limit


def test_presence_without_an_authority_is_unknown_not_offline() -> None:
    snapshot = _snapshot(
        devices=[
            RuntimeDevice(
                device_id="dev-quiet",
                name="书房音箱",
                role="对话身体",
                role_kind="persona",
                kind="esp32-s3",
                # The console shows this composed label; it is not a presence
                # authority, and the contract admits only two.
                status="online",
                online=True,
                approved=True,
                capabilities=[],
                signals={"presence_source": "hub+data"},
            )
        ]
    )
    payload = owner_runtime_projection(
        RuntimeComposition(snapshot=snapshot, ledger=_healthy_ledger()),
        audit_events=[_audit()],
    )
    _validator().validate(payload)
    presence = payload["devices"]["items"][0]["presence"]
    assert presence["source"] == "none"
    assert presence["state"] == "unknown"


def test_the_events_lane_is_the_audit_index_and_the_cursor_is_real() -> None:
    payload = owner_runtime_projection(
        RuntimeComposition(snapshot=_snapshot(), ledger=_healthy_ledger()),
        audit_events=[_audit(seq=41), _audit(seq=42)],
    )
    _validator().validate(payload)
    # Every moment has its place in this Host's order, and the cursor is the
    # highest one this reading actually carried — so a client resuming from it
    # gets what happened after what it was shown.
    assert [row["ingest_seq"] for row in payload["events"]["items"]] == [41, 42]
    assert payload["cursor"] == {"ingest_seq": 42}


def test_without_an_audit_index_the_events_lane_says_so() -> None:
    ledger = _healthy_ledger()
    ledger.record("audit.index", ok=False, detail="这台 Host 没有审计索引：事件流没有在跑")

    payload = owner_runtime_projection(
        RuntimeComposition(snapshot=_snapshot(), ledger=ledger),
        audit_events=[],
    )
    _validator().validate(payload)
    assert payload["events"]["state"] == "unavailable"
    assert "审计索引" in payload["events"]["detail"]
    # Nothing to resume from, and no number pretending otherwise.
    assert "cursor" not in payload
    # And the rest of the map is still drawn.
    assert payload["devices"]["state"] == "ok"


def test_every_source_the_composition_reads_decides_a_lane() -> None:
    """The registry is total, checked against the composition's own source code.

    A new read that reports under an unregistered label raises at runtime — but
    only on the path that exercises it, and a Host in the field is a poor place
    to find that out. This reads the labels out of the file instead.
    """

    service = (
        Path(__file__).resolve().parents[1]
        / "eidolon_admin_server"
        / "app"
        / "mission_control"
        / "service.py"
    ).read_text()
    labels = set(re.findall(r'ledger\.record\(\s*\n?\s*"([^"]+)"', service))
    labels |= set(re.findall(r'_(?:safe|unexposed)\(\s*ledger,\s*\n?\s*"([^"]+)"', service))
    assert labels, "no ledger.record labels found — did the composition stop reporting?"
    unregistered = sorted(labels - set(SOURCE_LANES))
    assert not unregistered, (
        f"these sources decide no lane: {unregistered}. Add them to SOURCE_LANES "
        "in app/mission_control/lanes.py."
    )


def test_an_unregistered_source_is_refused_rather_than_ignored() -> None:
    with pytest.raises(UnregisteredSource):
        LaneLedger().record("something.new", ok=False, detail="boom")
