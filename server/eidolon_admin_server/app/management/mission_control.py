"""The Owner's runtime map, projected from the one composition this Host makes.

Mission Control already existed here — as the operator console's reading, on
``/api/mission-control/*``, scoped by an ``owner_id`` query parameter. That
surface is not the Owner's: it carries the runtime blackboard, trace spans,
evidence chains and a permission ledger, and it lets its caller name whichever
Owner it likes. So the Owner's map is a **projection**, not a re-export:

* the reading is the same one (:func:`compose_runtime`), so the console and the
  phone can never disagree about what was observed;
* every lane arrives in the envelope the contract requires — state, reason,
  when, how long, whether it was cut short — taken from the
  :class:`LaneLedger`, which recorded each source's outcome against the lanes it
  decides at the point of the read;
* identity is absent on purpose. ``/context`` owns the Owner and the default
  pointer, the roster owns which Companions exist; this answer is only about
  what was observed of ids the caller already holds. A second place that names
  Companions is a second place that can be wrong about them.

The shape is
``eidolon_sdk/contracts/mission_control/v1/mission-control-snapshot.schema.json``
and the vocabulary is :mod:`eidolon_sdk.biz.contracts.mission_control` — imported
rather than restated, so a value cannot drift here without drifting there.
``test_owner_runtime_projection.py`` validates what this builds against that
schema, which is the same schema the phone's parser is pinned to by golden.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from eidolon_sdk.biz.contracts import mission_control as contract

from ...audit import IndexedAuditEvent
from ..mission_control.lanes import LANES, Lane, LaneLedger, LaneOutcome
from ..mission_control.service import RuntimeComposition

#: How many rows a lane will carry. A phone drawing a constellation needs the
#: shape of what is happening, not an archive of it — and a lane that stops
#: early says so through ``truncated`` rather than quietly ending.
LANE_LIMITS: dict[str, int] = {
    "devices": 64,
    "activities": 24,
    "turns": 24,
    "jobs": 24,
    "services": 32,
    "events": 120,
}


def owner_runtime_projection(
    composition: RuntimeComposition,
    *,
    audit_events: list[IndexedAuditEvent] | None = None,
) -> dict[str, Any]:
    """The Owner-scoped runtime snapshot, lane by lane.

    ``audit_events`` is the events lane, read from the audit index by the caller
    — the one lane whose source is not the console's composition. It is the
    index's total order that makes the lane resumable, and the caller reads it
    because the ledger has to hear about that read too.
    """

    snapshot = composition.snapshot
    ledger = composition.ledger
    observed_at = _stamp(snapshot.generated_at)
    outcomes = ledger.outcomes(observed_at=snapshot.generated_at)

    devices = [_device(row) for row in snapshot.devices]
    activities = [_activity(row) for row in snapshot.activities]
    turns = [_turn(row) for row in snapshot.recent_turns]
    jobs = [_job(row) for row in snapshot.jobs]
    services = [_service(row) for row in snapshot.services]
    indexed = list(audit_events or [])
    events = [_event(row) for row in indexed]
    # The cursor is the highest sequence this reading actually carried, so a
    # client resuming from it gets what happened after what it was shown — not
    # after whatever the index had reached while this answer was being built.
    cursor = max((row.ingest_seq for row in indexed), default=None)

    return {
        "contract_version": contract.CONTRACT_VERSION,
        "coverage": contract.SNAPSHOT_COVERAGE,
        "generated_at": observed_at,
        **(
            {"cursor": {contract.CURSOR_FIELD: cursor}}
            if cursor is not None
            # Omitted rather than zeroed. There is nothing to resume from when
            # nothing was read, and a cursor of 0 would claim otherwise.
            else {}
        ),
        "devices": _items_lane(outcomes["devices"], devices, "devices", ledger),
        "activities": _items_lane(outcomes["activities"], activities, "activities", ledger),
        "turns": _items_lane(outcomes["turns"], turns, "turns", ledger),
        "jobs": _items_lane(outcomes["jobs"], jobs, "jobs", ledger),
        "memory": _memory_lane(outcomes["memory"], snapshot.memory),
        "services": _items_lane(outcomes["services"], services, "services", ledger),
        "events": _items_lane(outcomes["events"], events, "events", ledger),
    }


def _items_lane(
    outcome: LaneOutcome,
    items: list[dict[str, Any]],
    lane: Lane,
    ledger: LaneLedger,
) -> dict[str, Any]:
    """One lane, its health and its rows together.

    An unreadable lane carries no rows even if some were composed: whatever is
    in there was not observed on this reading, and shipping it would make the
    failure invisible — which is the one thing every part of this contract
    exists to prevent.
    """

    if not outcome.readable:
        return _envelope(outcome, items=[])
    limit = LANE_LIMITS.get(lane)
    truncated = outcome.truncated
    if limit is not None and len(items) > limit:
        items = items[:limit]
        truncated = True
    return _envelope(outcome, items=items, truncated=truncated)


def _memory_lane(outcome: LaneOutcome, memory: Any) -> dict[str, Any]:
    """Memory is one summary, not a list, so it carries ``value``."""

    if not outcome.readable:
        return {**_head(outcome), "value": None}
    return {
        **_head(outcome),
        "value": {
            "realms_total": memory.realms_total,
            "active_realm_id": memory.active_realm_id,
            "runners_total": memory.runners_total,
            "runners_online": memory.runners_online,
            # No last_recall_hits. This lane is per Owner; a Companion's own
            # recall comes off its turn, and the same number in two places is
            # two numbers that can disagree.
            "last_write_disposition": memory.last_write_disposition or "",
        },
    }


def _envelope(
    outcome: LaneOutcome,
    *,
    items: list[dict[str, Any]],
    truncated: bool | None = None,
) -> dict[str, Any]:
    return {
        **_head(outcome),
        "truncated": outcome.truncated if truncated is None else truncated,
        "items": items,
    }


def _head(outcome: LaneOutcome) -> dict[str, Any]:
    return {
        "state": outcome.state,
        "detail": outcome.detail,
        "observed_at": _stamp(outcome.observed_at),
        "latency_ms": outcome.latency_ms,
    }


def _device(row: Any) -> dict[str, Any]:
    signals = row.signals if isinstance(row.signals, dict) else {}
    source = signals.get("presence_source") or contract.PRESENCE_SOURCE_NONE
    if source not in contract.PRESENCE_SOURCES:
        # Hub and the blackboard are the only authorities the contract admits.
        # Anything else — including the composed "hub+data" label the console
        # shows — is not a presence source, and saying ``none`` is the truth
        # about where presence came from rather than a guess dressed up as one.
        source = contract.PRESENCE_SOURCE_NONE
    state = row.status if row.status in contract.PRESENCE_STATES else (
        contract.PRESENCE_ONLINE if row.online else contract.PRESENCE_UNKNOWN
    )
    if source == contract.PRESENCE_SOURCE_NONE:
        # Nobody with standing answered. Not offline — unknown.
        state = contract.PRESENCE_UNKNOWN
    return {
        "device_id": row.device_id,
        "display_name": row.name,
        "device_kind": row.kind,
        "role": row.role,
        "role_kind": row.role_kind if row.role_kind in contract.ROLE_KINDS else "unbound",
        "companion_id": row.companion_id,
        "capabilities": list(row.capabilities),
        "presence": {
            "state": state,
            "source": source,
            "observed_at": _stamp(row.last_seen_at),
        },
    }


def _activity(row: Any) -> dict[str, Any]:
    return {
        "activity_id": row.activity_id,
        "kind": row.kind,
        "companion_id": row.companion_id,
        "status": row.status,
        "outcome": row.outcome,
        "summary": row.summary,
        "turn_id": row.turn_id,
        # No job_id. The contract does not carry one on an activity and nothing
        # reads one, and a field a consumer cannot use is a promise this Host
        # would be making on the next reader's behalf.
        "origin_device_id": row.origin_device_id,
        "target_device_ids": list(row.target_device_ids),
        "current_hop_id": row.current_hop_id,
        "started_at": _stamp(row.started_at),
        "updated_at": _stamp(row.updated_at),
        "finished_at": _stamp(row.finished_at),
        "route": [
            {
                "hop_id": hop.hop_id,
                "node_type": hop.node_type,
                "node_id": hop.node_id,
                "label": hop.label,
                "stage": hop.stage,
                "status": hop.status,
                "direction": hop.direction,
                "ts": _stamp(hop.ts),
                "latency_ms": hop.latency_ms,
            }
            for hop in row.route
        ],
    }


def _turn(row: Any) -> dict[str, Any]:
    return {
        "turn_id": row.turn_id,
        "companion_id": row.companion_id,
        "device_id": row.device_id,
        "status": row.status,
        "trigger": row.trigger,
        "latency_ms": row.latency_ms,
        "memory_hits": row.memory_hits,
        "tool_names": list(row.tool_names),
        "stages": [_stage(stage) for stage in row.stages],
        # Where the time went, which the Agent measured and nothing read. Not a
        # second copy of the stages: a stage is a place on the map, a breakdown
        # is a measurement.
        "breakdown": [_breakdown(row) for row in row.breakdown],
    }


def _stage(stage: Any) -> dict[str, Any]:
    raw = stage if isinstance(stage, dict) else {}
    return {
        "key": str(raw.get("key", "")),
        "label": str(raw.get("label", "")),
        "status": str(raw.get("status", "")),
        "latency_ms": raw.get("latency_ms"),
    }


def _breakdown(entry: Any) -> dict[str, Any]:
    raw = entry if isinstance(entry, dict) else {}
    return {
        "key": str(raw.get("key", "")),
        "label": str(raw.get("label", "")),
        # Null survives. A phase the turn never reached is not a phase that took
        # no time, and a zero here would read as the second one.
        "latency_ms": raw.get("latency_ms"),
    }


def _job(row: Any) -> dict[str, Any]:
    return {
        "job_id": row.job_id,
        "companion_id": row.companion_id,
        "kind": row.kind,
        "status": row.status,
        "summary": row.summary,
    }


def _service(row: Any) -> dict[str, Any]:
    return {
        "service_id": row.service_id,
        "display_name": row.name,
        "code": row.service_id,
        "mode": "",
        "tier": "service",
        "online": row.online,
        "checked": row.checked,
        "latency_ms": row.latency_ms,
        "detail": row.detail,
    }


def _event(row: IndexedAuditEvent) -> dict[str, Any]:
    """One audit event, as a moment on the Owner's map.

    The events contract is the audit envelope's vocabulary by reference —
    severity, outcome and privacy all ``$ref`` it — plus this index's
    ``ingest_seq``. So this is a rename, not a reinterpretation: ``producer`` is
    where it came from, ``action`` is what happened, ``reason`` is what to say
    about it.
    """

    envelope = row.envelope
    payload = envelope.payload if isinstance(envelope.payload, dict) else {}
    subject_type = envelope.subject_type
    subject_id = envelope.subject_id

    def _id(kind: str, *keys: str) -> str | None:
        # The subject is whatever this event was about; anything else it touched
        # is in the payload. Neither is guaranteed, and a missing id is a real
        # answer — this moment simply was not about a Companion, or a body.
        if subject_type == kind and subject_id:
            return subject_id
        for key in keys:
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
        return None

    return {
        "event_id": envelope.event_id,
        "ingest_seq": row.ingest_seq,
        "producer_seq": envelope.producer_seq,
        "ts": _stamp(envelope.occurred_at),
        "source": envelope.producer,
        "type": envelope.action,
        "severity": envelope.severity,
        "outcome": envelope.outcome,
        "privacy": envelope.data_classification,
        # Read from an index, which is this Host's own record of what happened.
        "origin": "live",
        "trace_id": envelope.trace_id,
        "companion_id": _id("companion", "companion_id"),
        "device_id": _id("device", "device_id"),
        "turn_id": _id("turn", "turn_id"),
        "job_id": _id("job", "job_id", "task_id"),
        "milestone": str(payload.get("milestone") or ""),
        "summary": envelope.reason or envelope.action,
    }


def _stamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    when = value if value.tzinfo else value.replace(tzinfo=UTC)
    return when.astimezone(UTC).isoformat().replace("+00:00", "Z")


__all__ = ["LANES", "LANE_LIMITS", "owner_runtime_projection"]
