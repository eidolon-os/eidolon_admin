"""Runtime aggregation for Mission Control.

Read-only, and second-hand on purpose: it asks the authorities over HTTP and
composes what they answer into one view for a large screen. It does not open a
database. It used to, which is why it was removed when Admin's boundary was
drawn, and why restoring it meant rewriting this file rather than checking it
out.

Some of what the original showed has no authority answering for it yet —
conversations, jobs, Guard bindings, the full Companion roster, the Owner
list. Those lanes report themselves unavailable with a reason rather than
rendering as an Owner with nothing happening. A cockpit that cannot tell
"quiet" from "not wired up" is worse than no cockpit.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx
from fastapi import Request

from ..control_plane.contracts import OwnerIdentity
from eidolon_sdk.biz.body import (
    DEVICE_BLACKBOARD_BUCKET,
    OwnerDeviceBlackboardSnapshot,
    RuntimeDeviceEntry,
    owner_device_blackboard_key,
)

from .schemas import (
    EvidenceChain,
    EvidenceStep,
    PermissionLedgerItem,
    RuntimeActivity,
    RuntimeTraceSpan,
    RuntimeCapabilityCard,
    RuntimeCompanion,
    RuntimeDevice,
    RuntimeDeviceBlackboard,
    RuntimeEvent,
    RuntimeExperience,
    RuntimeJob,
    RuntimeLane,
    RuntimeLaneItem,
    RuntimeMemory,
    RuntimeOwner,
    RuntimeRouteHop,
    RuntimeService,
    RuntimeSnapshot,
    RuntimeStoryStep,
    RuntimeTurn,
    SourceStatus,
)

_TEXT_KEYS = {
    "content",
    "text",
    "transcript",
    "user_text",
    "assistant_text",
    "prompt",
    "image",
    "image_url",
    "raw_image",
    "audio",
}


async def build_snapshot(
    request: Request, owner_id: str | None = None, demo_mode: str = "live"
) -> RuntimeSnapshot:
    started = time.perf_counter()
    generated_at = datetime.now(UTC)
    statuses: list[SourceStatus] = []
    control_plane = _control_plane(request)
    if control_plane is None:
        return RuntimeSnapshot(
            generated_at=generated_at,
            source_status=[
                SourceStatus(
                    source="control-plane",
                    ok=False,
                    detail="Admin has no control-plane clients configured",
                )
            ],
        )

    owner = await _select_owner(control_plane, owner_id, statuses)
    if owner is None:
        return RuntimeSnapshot(generated_at=generated_at, source_status=statuses)
    owner_id = owner.owner_id

    # Each lane comes from whichever authority owns it now, over HTTP. Four of
    # them have no authority answering yet — see _unexposed: they are reported
    # as unavailable with the reason, not as an Owner with nothing happening.
    companion_snapshot, device_page, data_events = await asyncio.gather(
        _safe(
            statuses,
            "data.companions",
            data_authority_of(control_plane).get_owner_default_runtime(owner_id),
            None,
        ),
        _safe(
            statuses,
            "hub.devices",
            hub_authority_of(control_plane).list_devices(owner_id=owner_id),
            [],
        ),
        _safe(
            statuses,
            "hub.events",
            hub_authority_of(control_plane).list_events(owner_id=owner_id, limit=240),
            [],
        ),
    )
    # Hub answers with a page; the merge below wants rows. This is the Owner's
    # own inventory — _hub_devices supplies Hub's global list with presence,
    # and the merge keeps rows that this Owner is not proven to own out of it.
    devices = _device_rows(device_page)
    companions = _companions_from_snapshot(companion_snapshot)
    if companion_snapshot is not None and not companions:
        statuses.append(
            SourceStatus(
                source="data.companions",
                ok=False,
                detail="only the primary Companion is published; the full roster is not",
            )
        )
    conversations = _unexposed(
        statuses,
        "data.conversations",
        "Data publishes no conversation history over HTTP",
    )
    memory_realms = _unexposed(
        statuses,
        "data.memory",
        "Memory publishes recollections, not the realm roster this panel needs",
    )
    jobs = _unexposed(statuses, "data.jobs", "Data publishes no job list over HTTP")
    guard_bindings = _unexposed(
        statuses,
        "data.guard_bindings",
        "the Guard runtime does not exist yet, so nothing binds a face to a device",
    )

    default_companion_id = getattr(owner, "default_companion_id", None)
    companion = _default_companion(companions, default_companion_id)
    guard_device_ids = frozenset(_active_guard_bindings(guard_bindings))
    runtime_blackboard = await _runtime_blackboard(request, owner_id, statuses)
    hub_devices = await _hub_devices(request, statuses)
    runtime_devices = _merge_devices(
        devices,
        hub_devices,
        runtime_blackboard=runtime_blackboard,
        owner_id=owner_id,
        companions=companions,
        guard_device_ids=guard_device_ids,
    )
    services, service_statuses = await _services(request)
    statuses.extend(service_statuses)

    turns = await _agent_turns(request, owner_id, statuses)
    long_tasks = await _agent_long_tasks(request, owner_id, statuses)
    memory = await _memory_summary(request, memory_realms, turns, statuses)

    runtime_jobs = [_job(row) for row in jobs]
    runtime_jobs.extend(_long_task_job(row) for row in long_tasks)
    runtime_jobs = _dedupe_jobs(runtime_jobs)[:12]

    data_runtime_events = _enrich_event_scope(_events_from_data(data_events), runtime_devices)
    recent_events = list(data_runtime_events)
    recent_events.extend(_events_from_turns(turns[:5]))
    recent_events.extend(_events_from_jobs(runtime_jobs[:5]))
    recent_events = sorted(
        recent_events,
        key=lambda ev: _as_utc(ev.ts) or datetime.min.replace(tzinfo=UTC),
        reverse=True,
    )[:120]

    agent_runtime_turns = [_turn(row) for row in turns]
    runtime_turns = _project_runtime_turns(data_runtime_events, agent_runtime_turns)
    primary_voice_turn = _primary_active_voice_turn(runtime_turns)
    activities = _project_runtime_activities(runtime_turns, runtime_jobs, recent_events)

    source_status = _coalesce_statuses(statuses)
    experience = _experience(
        owner=owner,
        companion=companion,
        devices=runtime_devices,
        services=services,
        activities=activities,
        primary_voice_turn=primary_voice_turn,
        memory=memory,
        jobs=runtime_jobs,
        recent_events=recent_events,
        source_status=source_status,
    )
    trace_spans = [
        span
        for turn in runtime_turns[:12]
        for span in _trace_spans(turn)
    ]
    permission_ledger = _permission_ledger(recent_events)
    evidence_chains = _evidence_chains(
        companion=companion,
        devices=runtime_devices,
        memory=memory,
        jobs=runtime_jobs,
        ledger=permission_ledger,
    )

    return RuntimeSnapshot(
        generated_at=generated_at,
        owner=RuntimeOwner(
            owner_id=owner.owner_id,
            display_name=owner.display_name,
            # Left empty on purpose. The Owner row in the database carried a
            # kind; the workspace authority's OwnerIdentity does not publish
            # one, and inventing a value here would put a fact on a screen that
            # nothing stands behind.
            kind="",
            status=owner.lifecycle_state,
        ),
        companion=_runtime_companion(companion) if companion is not None else None,
        companions=[_runtime_companion(row) for row in companions],
        default_companion_id=default_companion_id,
        devices=runtime_devices,
        services=services,
        activities=activities,
        recent_turns=runtime_turns[:12],
        memory=memory,
        jobs=runtime_jobs,
        recent_events=recent_events,
        source_status=source_status,
        runtime_blackboard=runtime_blackboard,
        experience=experience,
        trace_spans=trace_spans,
        evidence_chains=evidence_chains,
        permission_ledger=permission_ledger,
        demo_mode=demo_mode,  # type: ignore[arg-type]
    )


def hub_event_to_runtime(raw: dict[str, Any]) -> RuntimeEvent:
    now = datetime.now(UTC)
    event_type = str(raw.get("type") or "hub.event")
    if event_type == "probe_cycle":
        detected = int(raw.get("detected") or 0)
        ignored = int(raw.get("ignored") or 0)
        return RuntimeEvent(
            event_id=f"mc-hub-{uuid4().hex}",
            ts=_parse_dt(raw.get("at")) or now,
            source="hub",
            event_origin="live",
            type="device.presence.probe_cycle",
            severity="info",
            summary=f"Hub probe detected {detected} known device(s)",
            payload={"detected": detected, "ignored": ignored},
        )
    if event_type == "command_updated":
        status = str(raw.get("status") or "unknown")
        raw_payload = raw.get("payload")
        op = str(
            raw.get("op")
            or (raw_payload.get("op") if isinstance(raw_payload, dict) else "")
            or "device.command"
        )
        severity = "error" if status in {"failed", "timeout", "rejected"} else "info"
        return RuntimeEvent(
            event_id=str(raw.get("command_id") or f"mc-hub-{uuid4().hex}"),
            ts=_parse_dt(raw.get("updated_at") or raw.get("created_at")) or now,
            source="hub",
            event_origin="live",
            type="device.command.updated",
            severity=severity,  # type: ignore[arg-type]
            device_id=_str_or_none(raw.get("device_id")),
            summary=f"{op} -> {status}",
            payload=_safe_payload(
                {
                    "command_id": raw.get("command_id"),
                    "op": op,
                    "status": status,
                    "qos": raw.get("qos"),
                    "priority": raw.get("priority"),
                    "error": raw.get("error"),
                }
            ),
        )
    return RuntimeEvent(
        event_id=f"mc-hub-{uuid4().hex}",
        ts=now,
        source="hub",
        type=f"hub.{event_type}",
        severity="info",
        summary=f"Hub event: {event_type}",
        payload=_safe_payload(raw),
    )


def _enrich_event_scope(
    events: list[RuntimeEvent],
    devices: list[RuntimeDevice],
) -> list[RuntimeEvent]:
    """Attach owner/companion scope from the authoritative device binding.

    Hub and older audit events frequently carry only ``device_id``. Resolving
    that identity here keeps every consumer on one attribution rule and leaves
    the producers and their hot paths untouched.
    """

    by_id = {device.device_id: device for device in devices}
    enriched: list[RuntimeEvent] = []
    for event in events:
        device = by_id.get(event.device_id or "")
        if device is None:
            enriched.append(event)
            continue
        enriched.append(
            event.model_copy(
                update={
                    "owner_id": event.owner_id or device.owner_id,
                    "companion_id": event.companion_id or device.companion_id,
                }
            )
        )
    return enriched


async def enrich_runtime_event(
    request: Request,
    event: RuntimeEvent,
) -> RuntimeEvent:
    """Resolve a live event's scope without changing the originating system."""

    update: dict[str, Any] = {}
    if event.device_id and not event.companion_id and event.owner_id:
        control_plane = _control_plane(request)
        if control_plane is not None:
            # Hub is the device authority, and it answers per Owner rather than
            # per device — so an event that does not say whose it is cannot be
            # enriched, and is returned as it arrived rather than guessed at.
            try:
                page = await hub_authority_of(control_plane).list_devices(
                    owner_id=event.owner_id
                )
            except Exception:  # noqa: BLE001 - enrichment is best effort
                page = None
            row = _device_named(page, event.device_id)
            if row is not None:
                update["owner_id"] = event.owner_id
                update["companion_id"] = getattr(row, "bound_companion_id", None)
    return event.model_copy(update=update) if update else event


def _device_named(page: Any, device_id: str) -> Any | None:
    for row in _device_rows(page):
        if getattr(row, "device_id", None) == device_id:
            return row
    return None


def _device_rows(page: Any) -> list[Any]:
    if page is None:
        return []
    rows = getattr(page, "devices", None)
    return list(rows) if rows is not None else list(page) if isinstance(page, list) else []


def _companions_from_snapshot(snapshot: Any) -> list[Any]:
    """The Companion roster, as far as one is published.

    Data exposes the Owner's primary Companion runtime snapshot and nothing
    wider. One Companion is not a roster, and the caller is told so rather than
    being handed a list that looks complete.
    """

    if snapshot is None:
        return []
    companion = getattr(snapshot, "companion", None) or snapshot
    return [companion] if getattr(companion, "companion_id", None) else []


#: The three authorities this projection reads, named once each.
#:
#: They used to be spelled inline as ``control_plane.data_authority``,
#: ``.hub_management`` and ``.workspace_authority`` — none of which exist on the
#: composed service. Owner selection swallowed its AttributeError and reported
#: "no Owner", so this whole cockpit answered with an empty snapshot on every
#: real Host while its tests stayed green against a differently-shaped stub.
#:
#: Accessors rather than inline attributes so that a single test can hand them
#: the real service and find out (see test_mission_control_composition.py).
def data_authority_of(control_plane: Any) -> Any:
    """Companion runtime and identity."""
    return control_plane.data


def workspace_authority_of(control_plane: Any) -> Any:
    """The Owner aggregate, including which Companion is the default."""
    return control_plane.workspace


def hub_authority_of(control_plane: Any) -> Any:
    """Devices and their events."""
    return control_plane.hub


def _control_plane(request: Request) -> Any | None:
    """The HTTP client layer every other Admin surface already goes through.

    This used to be ``request.app.state.data_store`` — a DataStore, opened on
    the product database. Admin does not do that any more: Data owns owners and
    companions, Hub owns devices and their events, and Admin is a projection
    that asks them. An architecture test enforces it, and it is the reason the
    original of this file could not simply be restored.
    """

    return getattr(request.app.state, "control_plane", None)


async def _select_owner(
    control_plane: Any, owner_id: str | None, statuses: list[SourceStatus]
) -> OwnerIdentity | None:
    """The Owner this snapshot is about.

    One Owner has to be named. The database version listed every Owner and
    picked the first active one; no authority publishes that list over HTTP, so
    a caller that does not say which Owner it means cannot be answered — and
    saying so is better than picking one and hoping.
    """

    if not owner_id:
        _unexposed(
            statuses,
            "data.owners",
            "no authority publishes an Owner list over HTTP; ask for one Owner by id",
        )
        return None
    try:
        return await workspace_authority_of(control_plane).get_owner(owner_id)
    except Exception as exc:  # noqa: BLE001
        statuses.append(SourceStatus(source="data.owners", ok=False, detail=str(exc)))
        return None


async def _hub_devices(request: Request, statuses: list[SourceStatus]) -> list[Any]:
    client = getattr(request.app.state, "hub_device_client", None)
    if client is None:
        statuses.append(SourceStatus(source="hub", ok=False, detail="Hub client unavailable"))
        return []
    started = time.perf_counter()
    try:
        rows = await client.list_devices()
    except Exception as exc:  # noqa: BLE001 - degraded view is expected in dev
        statuses.append(SourceStatus(source="hub", ok=False, detail=str(exc)))
        return []
    statuses.append(_status("hub", True, started, f"{len(rows)} devices"))
    return rows


async def _runtime_blackboard(
    request: Request,
    owner_id: str,
    statuses: list[SourceStatus],
) -> RuntimeDeviceBlackboard:
    """Read exactly one owner-scoped current snapshot directly from NATS KV."""

    key = owner_device_blackboard_key(owner_id)
    client = getattr(request.app.state, "nats_kv", None)
    if client is None:
        detail = "NATS KV client unavailable"
        statuses.append(SourceStatus(source="runtime.blackboard", ok=False, detail=detail))
        return RuntimeDeviceBlackboard(health="degraded", detail=detail, key=key)

    started = time.perf_counter()
    try:
        raw = await client.get_existing(DEVICE_BLACKBOARD_BUCKET, key)
    except Exception as exc:  # noqa: BLE001 - observatory must fail closed
        detail = f"NATS KV read failed: {exc}"
        statuses.append(SourceStatus(source="runtime.blackboard", ok=False, detail=detail))
        return RuntimeDeviceBlackboard(health="degraded", detail=detail, key=key)

    if raw is None:
        detail = "No current snapshot for selected owner"
        statuses.append(_status("runtime.blackboard", True, started, detail))
        return RuntimeDeviceBlackboard(health="empty", detail=detail, key=key)

    try:
        snapshot = OwnerDeviceBlackboardSnapshot.from_bytes(
            raw,
            expected_owner_id=owner_id,
        )
    except Exception as exc:  # noqa: BLE001 - malformed/foreign data fails closed
        detail = f"Invalid owner snapshot: {exc}"
        statuses.append(SourceStatus(source="runtime.blackboard", ok=False, detail=detail))
        return RuntimeDeviceBlackboard(health="degraded", detail=detail, key=key)

    now = datetime.now(UTC)
    if not snapshot.ready:
        detail = "Hub snapshot is not ready"
        health = "degraded"
        available = False
    elif snapshot.hub_lease_expires_at <= now:
        detail = "Hub snapshot lease expired"
        health = "degraded"
        available = False
    elif not snapshot.devices:
        detail = "Snapshot ready; no current runtime devices"
        health = "empty"
        available = True
    else:
        online = sum(1 for row in snapshot.devices.values() if row.is_online(now=now))
        detail = f"Snapshot ready; {online}/{len(snapshot.devices)} devices online"
        health = "healthy"
        available = True

    statuses.append(_status("runtime.blackboard", available, started, detail))
    return RuntimeDeviceBlackboard(
        health=health,
        available=available,
        detail=detail,
        key=key,
        snapshot=snapshot,
    )


async def _agent_turns(
    request: Request,
    owner_id: str,
    statuses: list[SourceStatus],
) -> list[dict[str, Any]]:
    started = time.perf_counter()
    try:
        body = await _service_json(
            request,
            "agent",
            "/conversations/turns",
            params={"owner_id": owner_id, "limit": 20},
            timeout=2.0,
        )
        rows = body.get("turns") if isinstance(body, dict) else []
        turns = rows if isinstance(rows, list) else []
        statuses.append(_status("agent.turns", True, started, f"{len(turns)} turns"))
        return [row for row in turns if isinstance(row, dict)]
    except Exception as exc:  # noqa: BLE001
        statuses.append(SourceStatus(source="agent.turns", ok=False, detail=str(exc)))
        return []


async def _agent_long_tasks(
    request: Request,
    owner_id: str,
    statuses: list[SourceStatus],
) -> list[dict[str, Any]]:
    started = time.perf_counter()
    try:
        body = await _service_json(
            request,
            "agent",
            "/long-tasks",
            params={"owner_id": owner_id, "limit": 20},
            timeout=2.0,
        )
        rows = body.get("tasks") if isinstance(body, dict) else []
        tasks = rows if isinstance(rows, list) else []
        statuses.append(_status("agent.long_tasks", True, started, f"{len(tasks)} tasks"))
        return [row for row in tasks if isinstance(row, dict)]
    except Exception as exc:  # noqa: BLE001
        statuses.append(SourceStatus(source="agent.long_tasks", ok=False, detail=str(exc)))
        return []


async def _memory_summary(
    request: Request,
    realms: list[Any],
    turns: list[dict[str, Any]],
    statuses: list[SourceStatus],
) -> RuntimeMemory:
    runners_total = 0
    runners_online = 0
    started = time.perf_counter()
    try:
        from eidolon_admin_server.app.memory.runners import list_runners

        payload = await list_runners()
        rows = payload.get("runners") if isinstance(payload, dict) else []
        if isinstance(rows, list):
            runners_total = len(rows)
            runners_online = sum(
                1
                for row in rows
                if isinstance(row, dict)
                and (
                    row.get("worker_running")
                    or row.get("agent_reachable")
                    or row.get("runtime_state") == "running"
                )
            )
        statuses.append(_status("memory.runners", True, started, f"{runners_online}/{runners_total} online"))
    except Exception as exc:  # noqa: BLE001
        statuses.append(SourceStatus(source="memory.runners", ok=False, detail=str(exc)))

    latest = _turn(turns[0]) if turns else None
    observability_summary = _dict_or_empty(turns[0].get("observability_summary")) if turns else {}
    memory_summary = _dict_or_empty(observability_summary.get("memory")) if turns else {}
    write_summary = _dict_or_empty(observability_summary.get("memory_write")) if turns else {}
    active_realm = ""
    for realm in realms:
        if getattr(realm, "status", "") == "active":
            active_realm = getattr(realm, "realm_id", "") or ""
            break
    if not active_realm and realms:
        active_realm = getattr(realms[0], "realm_id", "") or ""
    hit_count = int(memory_summary.get("hit_count") or (latest.memory_hits if latest else 0))
    disposition = _str_or_none(write_summary.get("disposition"))
    return RuntimeMemory(
        realms_total=len(realms),
        active_realm_id=active_realm,
        runners_total=runners_total,
        runners_online=runners_online,
        last_recall_hits=hit_count,
        last_write_disposition=disposition,
        fanout_allowed=bool(write_summary.get("fanout_allowed")),
        privacy_mode=_str_or_none(observability_summary.get("privacy_mode")) if turns else None,
        summary=(
            f"{hit_count} recall hit(s), write {disposition or 'pending'}"
            if turns
            else "Waiting for turn trace"
        ),
    )


async def _services(request: Request) -> tuple[list[RuntimeService], list[SourceStatus]]:
    registry = getattr(request.app.state, "registry", None)
    http_client: httpx.AsyncClient | None = getattr(request.app.state, "http_client", None)
    if registry is None or http_client is None:
        return [], [SourceStatus(source="services", ok=False, detail="registry unavailable")]

    # Pull live supervisord process state once, so process-only services
    # (channel, mementos) without an HTTP health surface still report real
    # liveness — consistent with the Overview page's composite verdict.
    sv_client = getattr(request.app.state, "supervisor_client", None)
    sv_by_full: dict[str, Any] = {}
    if sv_client is not None:
        try:
            infos = await sv_client.get_all_process_info()
            sv_by_full = {info.full_name: info for info in infos}
        except Exception:  # noqa: BLE001
            sv_by_full = {}

    def _supervisor_state(svc: Any) -> tuple[bool, bool, str]:
        """Returns (supervised, all_programs_running, statename_summary)."""
        sup = getattr(svc, "supervisor", None)
        if sup is None:
            return False, False, ""
        group = getattr(sup, "group", "") or ""
        programs = getattr(sup, "programs", []) or []
        states = []
        for prog in programs:
            full = f"{group}:{prog}" if group else prog
            info = sv_by_full.get(full)
            states.append(getattr(info, "statename", "UNKNOWN") if info else "UNKNOWN")
        return True, (bool(states) and all(s == "RUNNING" for s in states)), ", ".join(states)

    async def _probe(svc: Any) -> RuntimeService:
        started = time.perf_counter()
        url = _health_url(svc)
        http_ok: bool | None = None
        latency: float | None = None
        http_detail = ""
        if url:
            try:
                resp = await http_client.get(url, timeout=1.2, headers={"Connection": "close"})
                http_ok = resp.status_code < 500
                http_detail = f"HTTP {resp.status_code}"
            except Exception as exc:  # noqa: BLE001
                http_ok = False
                http_detail = str(exc)
            latency = round((time.perf_counter() - started) * 1000, 1)

        supervised, all_running, sv_summary = _supervisor_state(svc)

        # Composite: online if HTTP healthy OR supervisord reports all RUNNING.
        online = bool(http_ok) or (supervised and all_running)
        checked = bool(url) or supervised
        if url:
            detail = http_detail
        elif supervised:
            detail = f"supervisord: {sv_summary or 'unknown'}"
        else:
            detail = "no health probe"

        return RuntimeService(
            service_id=svc.id,
            name=svc.name,
            online=online,
            checked=checked,
            latency_ms=latency,
            detail=detail,
        )

    rows = list(await asyncio.gather(*(_probe(svc) for svc in registry.services)))

    # Shared infrastructure (NATS / LiveKit): supervised-only, not in the
    # service registry, so surface them from supervisord process state.
    for sid, name, full in (("nats", "NATS", "nats:nats-server"), ("livekit", "LiveKit", "livekit:livekit-server")):
        if any(r.service_id == sid for r in rows):
            continue
        info = sv_by_full.get(full)
        state = getattr(info, "statename", "UNKNOWN") if info else "UNKNOWN"
        rows.append(
            RuntimeService(
                service_id=sid,
                name=name,
                online=state == "RUNNING",
                checked=info is not None,
                latency_ms=None,
                detail=f"supervisord: {state}",
            )
        )

    ok = sum(1 for row in rows if row.online)
    return rows, [SourceStatus(source="services", ok=True, detail=f"{ok}/{len(rows)} online")]


async def _service_json(
    request: Request,
    service_id: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    timeout: float = 3.0,
) -> Any:
    registry = request.app.state.registry
    service = registry.get(service_id)
    if service is None or not service.base_url:
        raise RuntimeError(f"service {service_id!r} is not proxied")
    prefix = (service.upstream_prefix or "").rstrip("/")
    suffix = path if path.startswith("/") else f"/{path}"
    url = f"{service.base_url.rstrip('/')}{prefix}{suffix}"
    http_client: httpx.AsyncClient = request.app.state.http_client
    resp = await http_client.get(url, params=params, timeout=timeout, headers={"Connection": "close"})
    resp.raise_for_status()
    return resp.json()


async def _safe(
    statuses: list[SourceStatus],
    label: str,
    coro: Any,
    fallback: Any,
) -> Any:
    started = time.perf_counter()
    try:
        result = await coro
        statuses.append(_status(label, True, started))
        return result
    except Exception as exc:  # noqa: BLE001
        statuses.append(SourceStatus(source=label, ok=False, detail=str(exc)))
        return fallback


def _unexposed(statuses: list[SourceStatus], label: str, reason: str) -> list[Any]:
    """Record a lane this Host has no way to answer, and return nothing for it.

    Deliberately not the failure path above. A call that broke and a capability
    that was never exposed produce the same empty list, and on a screen they
    produce the same blank panel — but they are opposite facts, and only one of
    them is worth paging someone about.

    This cockpit could show conversations, jobs and Guard bindings when it read
    the database directly. It does not read the database any more, and the
    authorities that took that data over publish no HTTP surface for it. So the
    panel says that, rather than showing a Host with nothing happening on it.
    """

    statuses.append(SourceStatus(source=label, ok=False, detail=reason))
    return []


def _merge_devices(
    data_devices: list[Any],
    hub_devices: list[Any],
    *,
    runtime_blackboard: RuntimeDeviceBlackboard,
    owner_id: str,
    companions: list[Any] = (),
    guard_device_ids: frozenset[str] = frozenset(),
) -> list[RuntimeDevice]:
    companion_by_id = {
        getattr(c, "companion_id", ""): c
        for c in companions
        if getattr(c, "companion_id", "")
    }
    by_id: dict[str, dict[str, Any]] = {}
    for row in data_devices:
        by_id[row.device_id] = {"data": row, "hub": None, "runtime": None}
    runtime_rows: dict[str, RuntimeDeviceEntry] = {}
    if runtime_blackboard.available and runtime_blackboard.snapshot is not None:
        runtime_rows = dict(runtime_blackboard.snapshot.devices)
        for row in runtime_rows.values():
            by_id.setdefault(
                row.device_id,
                {"data": None, "hub": None, "runtime": None},
            )["runtime"] = row
    for row in hub_devices:
        # Hub's admin list is global. Only join rows already proven to belong
        # to this owner by the Data authority or the owner-isolated blackboard.
        if row.device_id not in by_id and getattr(row, "owner_id", None) != owner_id:
            continue
        by_id.setdefault(
            row.device_id,
            {"data": None, "hub": None, "runtime": None},
        )["hub"] = row

    devices = []
    for device_id, parts in by_id.items():
        data = parts["data"]
        hub = parts["hub"]
        runtime = parts["runtime"]
        name = (
            getattr(runtime, "name", "")
            or getattr(data, "name", "")
            or getattr(hub, "name", "")
            or device_id
        )
        kind = getattr(data, "kind", "") or getattr(hub, "kind", "") or "unknown"
        status, online, presence_source = _device_presence(runtime, hub)
        last_seen = _as_utc(
            getattr(runtime, "last_seen_at", None)
            or getattr(hub, "last_seen", None)
            or getattr(data, "last_seen_at", None)
        )
        capabilities = (
            sorted(capability.name for capability in runtime.capabilities)
            if online and runtime is not None
            else []
        )
        companion_id = (
            getattr(runtime, "provider_companion_id", None)
            or getattr(data, "bound_companion_id", None)
        )
        bound_companion = companion_by_id.get(companion_id) if companion_id else None
        is_guard = device_id in guard_device_ids or _is_guard_companion(bound_companion)
        role, role_kind = _device_role(bound_companion, is_guard)
        devices.append(
            RuntimeDevice(
                device_id=device_id,
                name=name,
                role=role,
                role_kind=role_kind,
                kind=kind,
                status=status,
                online=online,
                approved=bool(getattr(hub, "approved", False) or getattr(data, "approved_at", None)),
                owner_id=(
                    getattr(data, "owner_id", None)
                    or getattr(hub, "owner_id", None)
                    or (owner_id if runtime else None)
                ),
                companion_id=companion_id,
                interaction_mode=getattr(data, "interaction_mode", None),
                room_name=(
                    getattr(runtime, "room_name", "")
                    or getattr(hub, "room_name", "")
                ),
                participant_sid=(
                    getattr(runtime, "participant_sid", "")
                    or getattr(hub, "participant_sid", "")
                ),
                last_seen_at=last_seen,
                capabilities=capabilities,
                signals={
                    "missed_probes": getattr(hub, "missed_probes", 0),
                    "paired": bool(getattr(hub, "paired", False)),
                    "source": (
                        "runtime_blackboard"
                        if runtime is not None
                        else "hub+data"
                        if hub is not None and data is not None
                        else presence_source
                    ),
                    "presence_source": presence_source,
                    "blackboard_health": runtime_blackboard.health,
                    "blackboard_available": runtime_blackboard.available,
                    "blackboard_detail": runtime_blackboard.detail,
                    "registration_id": getattr(runtime, "registration_id", ""),
                    "manifest_revision": getattr(runtime, "manifest_revision", ""),
                    "presence_revision": getattr(runtime, "presence_revision", ""),
                },
            )
        )
    return sorted(devices, key=lambda item: (not item.online, item.role_kind, item.device_id))


def _device_presence(runtime: Any, hub: Any) -> tuple[str, bool, str]:
    """Project one device's presence without conflating source health.

    A usable owner-scoped blackboard entry is the richest runtime signal. If
    that entry is absent (including when the blackboard itself is degraded),
    Hub remains the per-device presence authority. The Data authority only
    proves inventory/ownership and must never turn its lifecycle status (for
    example ``active``) into an online state.
    """

    if runtime is not None:
        online = bool(runtime.is_online())
        # Runtime entries can retain status="online" briefly after their
        # device lease expires. Keep the public status consistent with the
        # lease-aware `online` verdict.
        return ("online" if online else "offline"), online, "runtime_blackboard"

    if hub is not None:
        status = str(getattr(hub, "status", "") or "").strip().lower()
        if status not in {"online", "degraded", "offline", "unknown"}:
            status = "offline"
        return status, status == "online", "hub"

    return "offline", False, "data"


def _is_guard_companion(companion: Any) -> bool:
    """Guard is what a Companion *is*, never a property of its hardware.

    One signal: ``kind``. The second clause here read ``companion_type``, which
    no Companion has — it was a fallback for a field that was itself derived
    from a non-existent one, so it could only ever be false.
    """
    if companion is None:
        return False
    return str(getattr(companion, "kind", "") or "") == "guard"


def _device_role(bound_companion: Any, is_guard: bool) -> tuple[str, str]:
    """A device's logical role comes from the companion it is bound to, not
    from its board `kind`. Returns (human_label, role_kind classifier)."""
    if is_guard:
        return "守护哨兵", "guard"
    if bound_companion is not None:
        return "对话身体", "persona"
    return "未绑定", "unbound"


# A guard binding no longer relevant to the device: revoked or replaced.
_GUARD_BINDING_TERMINAL_STATES = frozenset({"revoked", "replaced"})


def _active_guard_bindings(bindings: list[Any]) -> dict[str, str]:
    """Map guard-bound device_id → guard companion_id for live bindings.

    The device↔guard-companion binding lives in ``guard_bindings`` (not in the
    device's ``bound_companion_id``), so this is how a device's guard role is
    read from its binding rather than guessed from hardware or a capability."""
    result: dict[str, str] = {}
    for row in bindings:
        device_id = getattr(row, "device_id", "") or ""
        if not device_id:
            continue
        if getattr(row, "revoked_at", None) is not None:
            continue
        if str(getattr(row, "state", "") or "") in _GUARD_BINDING_TERMINAL_STATES:
            continue
        result[device_id] = getattr(row, "guard_companion_id", "") or ""
    return result


def _is_prepared_web_body(device: RuntimeDevice) -> bool:
    source = str(device.signals.get("source") or "").lower()
    return (
        not device.online
        and device.kind.lower() == "web"
        and (device.status == "active" or source in {"data", "hub+data"})
    )


def _device_lane_status(device: RuntimeDevice) -> str:
    if device.online:
        return "done"
    if _is_prepared_web_body(device):
        return "idle"
    if device.status == "degraded":
        return "degraded"
    return "idle"


def _device_lane_detail(device: RuntimeDevice) -> str:
    caps = "、".join(_friendly_capability(cap) for cap in device.capabilities[:4])
    if _is_prepared_web_body(device):
        suffix = f" · {caps}" if caps else ""
        return f"已准备，可启动{suffix}"
    if device.online:
        suffix = f" · {caps}" if caps else ""
        return f"在线{suffix}"
    if caps:
        return caps
    return "等待连接"


_CHANNEL_TERMINAL_TYPES = {
    "channel.turn.completed",
    "channel.turn.rejected",
    "channel.turn.failed",
}

_CHANNEL_STAGE_META: dict[str, tuple[str, str]] = {
    "user_speech_open": ("speech", "用户说话"),
    "provisional_duck": ("duck", "暂时压低播放"),
    "evidence_arbitration": ("eot", "判断打断与轮次结束"),
    "accepted_interruption": ("interrupt", "接受打断"),
    "rejected_interruption": ("interrupt", "恢复原播放"),
    "user_turn_pending": ("eot", "等待完整轮次"),
    "user_turn_committed": ("commit", "轮次已提交"),
    "user_turn_rejected": ("commit", "轮次未进入大脑"),
    "generating": ("brain", "大脑开始生成"),
    "brain_request_sent": ("brain", "请求已送达大脑"),
    "brain_first_delta": ("response", "大脑首个响应"),
    "brain_done": ("response", "大脑响应完成"),
    "brain_cancelled": ("response", "大脑响应被取消"),
    "brain_error": ("response", "大脑响应失败"),
    "llm_error": ("response", "生成失败"),
    "tts_provider_first_audio": ("tts", "TTS 首个音频"),
    "tts_error": ("tts", "TTS 生成失败"),
    "session_error": ("response", "会话运行失败"),
    "first_audio": ("playback", "开始播放"),
    "playback_done": ("playback", "播放完成"),
}


def _project_runtime_turns(
    events: list[RuntimeEvent],
    agent_turns: list[RuntimeTurn],
) -> list[RuntimeTurn]:
    """Merge Channel facts and Agent rows into one turn view keyed by trace."""

    grouped: dict[str, list[RuntimeEvent]] = {}
    session_boundaries: dict[str, list[RuntimeEvent]] = {}
    for event in events:
        if event.source != "channel":
            continue
        if event.type in {"channel.session.ended", "channel.session.failed"}:
            room_name = str(event.payload.get("room_name") or "")
            if room_name:
                session_boundaries.setdefault(room_name, []).append(event)
            continue
        if not event.type.startswith("channel.turn."):
            continue
        channel_turn_id = str(event.payload.get("channel_turn_id") or event.turn_id or "")
        if channel_turn_id:
            grouped.setdefault(channel_turn_id, []).append(event)

    agent_by_trace = {turn.trace_id: turn for turn in agent_turns if turn.trace_id}
    used_agent_ids: set[str] = set()
    projected: list[RuntimeTurn] = []
    for channel_turn_id, turn_events in grouped.items():
        ordered = sorted(turn_events, key=lambda event: _as_utc(event.ts) or datetime.min.replace(tzinfo=UTC))
        trace_id = next((event.trace_id for event in ordered if event.trace_id), channel_turn_id)
        agent_turn = agent_by_trace.get(trace_id)
        if agent_turn is not None:
            used_agent_ids.add(agent_turn.turn_id)
        terminal = next((event for event in reversed(ordered) if event.type in _CHANNEL_TERMINAL_TYPES), None)
        room_name = next(
            (str(event.payload.get("room_name") or "") for event in ordered if event.payload.get("room_name")),
            "",
        )
        last_event_at = _as_utc(ordered[-1].ts) if ordered else None
        session_boundary = None
        if terminal is None and room_name and last_event_at is not None:
            session_boundary = next(
                (
                    event
                    for event in sorted(
                        session_boundaries.get(room_name, []),
                        key=lambda item: _as_utc(item.ts) or datetime.min.replace(tzinfo=UTC),
                    )
                    if (_as_utc(event.ts) or datetime.min.replace(tzinfo=UTC)) >= last_event_at
                ),
                None,
            )
        phase_events = [event for event in ordered if event.type == "channel.turn.phase_changed"]
        latest_phase = str(phase_events[-1].payload.get("phase") or "") if phase_events else ""
        if terminal is not None:
            status = str(terminal.payload.get("status") or "completed")
            outcome = terminal.outcome
        elif session_boundary is not None:
            status = "failed" if session_boundary.type == "channel.session.failed" else "orphaned"
            outcome = "failure"
        else:
            status = "running"
            outcome = "deferred"
        started_at = _as_utc(ordered[0].ts) if ordered else None
        finished_at = _as_utc(terminal.ts) if terminal else (
            _as_utc(session_boundary.ts) if session_boundary is not None else None
        )
        elapsed_ms = (
            int((finished_at - started_at).total_seconds() * 1000)
            if started_at is not None and finished_at is not None
            else None
        )
        stages = _channel_turn_stages(
            ordered,
            terminal=terminal,
            orphaned=session_boundary is not None,
        )
        if agent_turn is not None:
            stages = _merge_agent_stages(stages, agent_turn.stages)
        projected.append(
            RuntimeTurn(
                turn_id=channel_turn_id,
                trace_id=trace_id,
                channel_turn_id=channel_turn_id,
                agent_turn_id=agent_turn.agent_turn_id if agent_turn else None,
                conversation_id=(
                    agent_turn.conversation_id
                    if agent_turn
                    else next(
                        (
                            str(event.payload.get("conversation_id"))
                            for event in reversed(ordered)
                            if event.payload.get("conversation_id")
                        ),
                        "",
                    )
                ),
                owner_id=ordered[-1].owner_id or (agent_turn.owner_id if agent_turn else ""),
                companion_id=ordered[-1].companion_id or (agent_turn.companion_id if agent_turn else ""),
                device_id=next((event.device_id for event in ordered if event.device_id), None)
                or (agent_turn.device_id if agent_turn else None),
                status=status,
                trigger="voice.full_duplex",
                started_at=started_at,
                finished_at=finished_at,
                latency_ms=elapsed_ms or (agent_turn.latency_ms if agent_turn else None),
                memory_hits=agent_turn.memory_hits if agent_turn else 0,
                tool_names=agent_turn.tool_names if agent_turn else [],
                privacy_mode=agent_turn.privacy_mode if agent_turn else "safe",
                phase=latest_phase,
                outcome=outcome,
                terminal_reason=(
                    str(terminal.payload.get("terminal_reason") or "")
                    if terminal
                    else (
                        "session_failed_without_turn_terminal"
                        if session_boundary is not None
                        and session_boundary.type == "channel.session.failed"
                        else "session_ended_without_turn_terminal"
                        if session_boundary is not None
                        else ""
                    )
                ),
                event_ids=[
                    *[event.event_id for event in ordered],
                    *([session_boundary.event_id] if session_boundary is not None else []),
                ],
                missing_milestones=(
                    [str(item) for item in terminal.payload.get("missing_milestones") or []]
                    if terminal
                    else ["terminal_event"] if session_boundary is not None else []
                ),
                stages=stages,
            )
        )

    projected.extend(turn for turn in agent_turns if turn.turn_id not in used_agent_ids)
    return sorted(
        projected,
        key=lambda turn: _as_utc(turn.started_at) or datetime.min.replace(tzinfo=UTC),
        reverse=True,
    )


def _channel_turn_stages(
    events: list[RuntimeEvent],
    *,
    terminal: RuntimeEvent | None,
    orphaned: bool = False,
) -> list[dict[str, Any]]:
    ordered_keys: list[str] = []
    stage_rows: dict[str, dict[str, Any]] = {}
    previous_elapsed = 0.0
    for event in events:
        semantic = ""
        if event.type == "channel.turn.phase_changed":
            semantic = str(event.payload.get("phase") or "")
        elif event.type == "channel.turn.milestone":
            semantic = str(event.payload.get("milestone") or "")
        meta = _CHANNEL_STAGE_META.get(semantic)
        if meta is None:
            continue
        key, label = meta
        elapsed = float(event.payload.get("elapsed_ms") or previous_elapsed)
        latency = max(0, round(elapsed - previous_elapsed))
        previous_elapsed = max(previous_elapsed, elapsed)
        if key not in stage_rows:
            ordered_keys.append(key)
            stage_rows[key] = {"key": key, "label": label, "status": "done", "latency_ms": latency}
        else:
            stage_rows[key]["label"] = label
            stage_rows[key]["latency_ms"] = int(stage_rows[key].get("latency_ms") or 0) + latency
    stages = [stage_rows[key] for key in ordered_keys]
    if not stages:
        return stages
    if orphaned:
        stages[-1]["status"] = "degraded"
    elif terminal is None:
        stages[-1]["status"] = "running"
    elif terminal.outcome in {"failure", "denied"}:
        stages[-1]["status"] = "failed" if terminal.outcome == "failure" else "degraded"
    return stages


def _merge_agent_stages(
    channel_stages: list[dict[str, Any]],
    agent_stages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not channel_stages:
        return list(agent_stages)
    seen = {str(stage.get("key") or "") for stage in channel_stages}
    extras = [
        dict(stage)
        for stage in agent_stages
        if str(stage.get("key") or "") in {"memory_recall", "tools", "memory_write"}
        and str(stage.get("key") or "") not in seen
    ]
    return [*channel_stages, *extras]


def _turn(row: dict[str, Any]) -> RuntimeTurn:
    obs = row.get("observability_summary") or {}
    memory = obs.get("memory") or {}
    tools = obs.get("tools") or {}
    latency = obs.get("latency") or {}
    stages = _turn_stages(row, obs)
    agent_turn_id = str(row.get("turn_id") or "")
    trace_id = _str_or_none(row.get("trace_id"))
    return RuntimeTurn(
        turn_id=agent_turn_id,
        trace_id=trace_id,
        agent_turn_id=agent_turn_id,
        conversation_id=str(row.get("conversation_id") or ""),
        owner_id=str(row.get("owner_id") or ""),
        companion_id=str(row.get("companion_id") or ""),
        device_id=_str_or_none(row.get("device_id")),
        status=str(row.get("status") or "unknown"),
        trigger=str(row.get("trigger") or ""),
        started_at=_parse_dt(row.get("started_at")),
        finished_at=_parse_dt(row.get("finished_at")),
        latency_ms=_int_or_none(latency.get("total_ms") or row.get("total_latency_ms")),
        memory_hits=int(memory.get("hit_count") or 0),
        tool_names=[str(item) for item in tools.get("names") or []],
        privacy_mode=_str_or_none(obs.get("privacy_mode")),
        phase="agent_turn",
        outcome="failure" if str(row.get("status") or "").lower() in {"failed", "errored", "error"} else "success",
        stages=stages,
    )


def _turn_stages(row: dict[str, Any], obs: dict[str, Any]) -> list[dict[str, Any]]:
    memory = obs.get("memory") or {}
    write = obs.get("memory_write") or {}
    tools = obs.get("tools") or {}
    latency = obs.get("latency") or {}
    return [
        {
            "key": "input",
            "label": "确认是谁在和哪个身体说话",
            "status": "done",
            "latency_ms": None,
        },
        {
            "key": "memory_recall",
            "label": f"查找相关记忆：{memory.get('hit_count') or 0} 条",
            "status": "degraded" if memory.get("degraded") else ("done" if memory.get("attempted") else "pending"),
            "latency_ms": None,
        },
        {
            "key": "agent_turn",
            "label": f"智能体思考并回应：{_friendly_status(row.get('status'))}",
            "status": _stage_status(row.get("status")),
            "latency_ms": latency.get("total_ms") or row.get("total_latency_ms"),
        },
        {
            "key": "tools",
            "label": f"调用工具或外部能力：{tools.get('count') or 0} 次",
            "status": "failed" if (tools.get("error_count") or 0) else "done",
            "latency_ms": tools.get("total_latency_ms"),
        },
        {
            "key": "memory_write",
            "label": f"决定是否写入长期记忆：{_friendly_memory_write(write.get('disposition'))}",
            "status": "done" if write.get("fanout_allowed") else "pending",
            "latency_ms": None,
        },
    ]


def _stage_status(status: Any) -> str:
    value = str(status or "").lower()
    if value in {"ok", "done", "succeeded", "completed"}:
        return "done"
    if value in {"failed", "errored", "error"}:
        return "failed"
    if value in {"running", "pending", "queued"}:
        return "running"
    return "done" if value else "pending"


_ACTIVE_ACTIVITY_STATES = {
    "running",
    "active",
    "pending",
    "queued",
    "accepted",
    "processing",
    "generating",
    "speaking",
    "deferred",
}


def _primary_active_voice_turn(turns: list[RuntimeTurn]) -> RuntimeTurn | None:
    """Choose one voice turn only for legacy voice-specific summary cards.

    Concurrent runtime state is represented by ``RuntimeActivity[]``. This
    helper must never be used as a snapshot-wide playhead.
    """

    for turn in turns:
        if _activity_is_active(turn.status):
            return turn
    return None


def _activity_is_active(status: str) -> bool:
    return status.lower() in _ACTIVE_ACTIVITY_STATES


def _project_runtime_activities(
    turns: list[RuntimeTurn],
    jobs: list[RuntimeJob],
    events: list[RuntimeEvent],
) -> list[RuntimeActivity]:
    """Project independent runtime facts into concurrent observer lanes."""

    activities = [_voice_activity(turn) for turn in turns[:12]]
    activities.extend(_job_activity(job) for job in jobs[:12])
    activities.extend(
        activity
        for event in events
        if (activity := _event_activity(event)) is not None
    )
    ordered = sorted(
        activities,
        key=lambda item: (
            _activity_is_active(item.status),
            _as_utc(item.updated_at or item.started_at) or datetime.min.replace(tzinfo=UTC),
        ),
        reverse=True,
    )
    # A noisy device must not evict every other companion from the observatory.
    # This is display back-pressure only; the persisted event ledger stays whole.
    bounded: list[RuntimeActivity] = []
    per_scope: dict[str, int] = {}
    for activity in ordered:
        scope = activity.companion_id or f"owner:{activity.owner_id}"
        if per_scope.get(scope, 0) >= 12 and not _activity_is_active(activity.status):
            continue
        bounded.append(activity)
        per_scope[scope] = per_scope.get(scope, 0) + 1
        if len(bounded) >= 48:
            break
    return bounded


def _voice_activity(turn: RuntimeTurn) -> RuntimeActivity:
    route: list[RuntimeRouteHop] = []
    if turn.device_id:
        route.append(
            RuntimeRouteHop(
                hop_id=f"voice:{turn.turn_id}:device-in",
                node_type="device",
                node_id=turn.device_id,
                label=turn.device_id,
                stage="input",
                status="done",
                direction="in",
                ts=turn.started_at,
            )
        )
    if turn.companion_id:
        route.append(
            RuntimeRouteHop(
                hop_id=f"voice:{turn.turn_id}:companion",
                node_type="companion",
                node_id=turn.companion_id,
                label="Companion",
                stage="identity",
                status="done",
                direction="in",
                ts=turn.started_at,
            )
        )
    for index, stage in enumerate(turn.stages):
        key = str(stage.get("key") or f"stage-{index}")
        node_type, node_id, direction = _voice_stage_node(key, turn.device_id)
        route.append(
            RuntimeRouteHop(
                hop_id=f"voice:{turn.turn_id}:{key}:{index}",
                node_type=node_type,
                node_id=node_id,
                label=str(stage.get("label") or key),
                stage=key,
                status=str(stage.get("status") or "pending"),
                direction=direction,
                latency_ms=_int_or_none(stage.get("latency_ms")),
            )
        )
    active = _activity_is_active(turn.status)
    current = next((hop.hop_id for hop in reversed(route) if hop.status == "running"), None)
    if active and current is None and route:
        current = route[-1].hop_id
    playback_seen = any(hop.stage in {"tts", "playback"} for hop in route)
    return RuntimeActivity(
        activity_id=f"voice:{turn.turn_id}",
        kind="voice_turn",
        owner_id=turn.owner_id,
        companion_id=turn.companion_id or None,
        trace_id=turn.trace_id,
        turn_id=turn.turn_id,
        origin_device_id=turn.device_id,
        target_device_ids=[turn.device_id] if turn.device_id and playback_seen else [],
        status=turn.status,
        outcome=turn.outcome,
        summary=_voice_activity_summary(turn),
        current_hop_id=current,
        started_at=turn.started_at,
        updated_at=turn.finished_at or turn.started_at,
        finished_at=turn.finished_at,
        event_ids=turn.event_ids,
        route=route,
    )


def _voice_stage_node(stage: str, device_id: str | None) -> tuple[str, str, str]:
    if stage == "playback" and device_id:
        return "device", device_id, "out"
    if stage in {"memory_recall", "memory_write"}:
        return "memory", "memory", "out"
    if stage in {"agent_turn", "brain", "response", "tools"}:
        return "service", "agent", "internal"
    return "service", "channel", "out" if stage == "tts" else "in"


def _voice_activity_summary(turn: RuntimeTurn) -> str:
    if _activity_is_active(turn.status):
        stage = next(
            (str(item.get("label") or "") for item in reversed(turn.stages) if item.get("status") == "running"),
            "语音轮次进行中",
        )
        return stage
    if turn.status == "interrupted":
        return "语音轮次已被用户打断"
    if turn.outcome == "failure":
        return "语音轮次失败"
    return "语音轮次已完成"


def _job_activity(job: RuntimeJob) -> RuntimeActivity:
    active = _activity_is_active(job.status)
    route = [
        RuntimeRouteHop(
            hop_id=f"job:{job.job_id}:agent",
            node_type="service",
            node_id="agent",
            label="智能体任务编排",
            stage="dispatch",
            status="done" if not active else "running",
            direction="out",
            ts=job.created_at,
        )
    ]
    provider_id = job.provider.lower() or "agent"
    if provider_id != "agent":
        route.append(
            RuntimeRouteHop(
                hop_id=f"job:{job.job_id}:provider",
                node_type="provider",
                node_id=provider_id,
                label=job.provider or job.kind or "后台执行器",
                stage="execute",
                status="running" if active else _stage_status(job.status),
                direction="out",
                ts=job.updated_at or job.created_at,
            )
        )
    return RuntimeActivity(
        activity_id=f"job:{job.job_id}",
        kind="background_job",
        owner_id=job.owner_id,
        companion_id=job.companion_id,
        turn_id=job.turn_id,
        job_id=job.job_id,
        status=job.status,
        outcome="deferred" if active else "failure" if job.status.lower() in {"failed", "error", "errored"} else "success",
        summary=job.summary or f"{job.provider}:{job.kind}",
        current_hop_id=route[-1].hop_id if active else None,
        started_at=job.created_at,
        updated_at=job.updated_at or job.created_at,
        finished_at=job.completed_at,
        route=route,
    )


def _event_activity(event: RuntimeEvent) -> RuntimeActivity | None:
    lowered = event.type.lower()
    if "command" in lowered:
        kind = "device_command"
        outbound = True
    elif lowered.startswith("guard.") or ".guard." in lowered:
        kind = "guard_event"
        outbound = False
    elif event.device_id and (event.source == "hub" or lowered.startswith("device.")):
        kind = "device_event"
        outbound = False
    else:
        return None

    route: list[RuntimeRouteHop] = []
    endpoints: list[tuple[str, str, str, str]] = []
    if outbound:
        if event.companion_id:
            endpoints.append(("companion", event.companion_id, "Companion", "out"))
        endpoints.append(("service", "hub", "设备中枢", "out"))
        if event.device_id:
            endpoints.append(("device", event.device_id, event.device_id, "out"))
    else:
        if event.device_id:
            endpoints.append(("device", event.device_id, event.device_id, "in"))
        endpoints.append(("service", "hub", "设备中枢", "in"))
        if event.companion_id:
            endpoints.append(("companion", event.companion_id, "Companion", "in"))
    status = str(event.payload.get("status") or "completed")
    active = _activity_is_active(status)
    for index, (node_type, node_id, label, direction) in enumerate(endpoints):
        route.append(
            RuntimeRouteHop(
                hop_id=f"event:{event.event_id}:{index}",
                node_type=node_type,
                node_id=node_id,
                label=label,
                stage="command" if outbound else "observe",
                status="running" if active and index == len(endpoints) - 1 else "done",
                direction=direction,
                ts=event.ts,
            )
        )
    return RuntimeActivity(
        activity_id=f"event:{event.event_id}",
        kind=kind,
        owner_id=event.owner_id or "",
        companion_id=event.companion_id,
        trace_id=event.trace_id,
        turn_id=event.turn_id,
        job_id=event.job_id,
        origin_device_id=event.device_id if not outbound else None,
        target_device_ids=[event.device_id] if outbound and event.device_id else [],
        status=status,
        outcome="deferred" if active else event.outcome,
        summary=event.summary,
        current_hop_id=route[-1].hop_id if active and route else None,
        started_at=event.ts,
        updated_at=event.ts,
        finished_at=None if active else event.ts,
        event_ids=[event.event_id],
        route=route,
    )


def _job(row: Any) -> RuntimeJob:
    progress = getattr(row, "progress_json", None) or {}
    result = getattr(row, "result_json", None) or {}
    return RuntimeJob(
        job_id=row.job_id,
        owner_id=row.owner_id,
        companion_id=row.companion_id,
        conversation_id=row.conversation_id,
        turn_id=row.turn_id,
        provider=row.provider,
        kind=row.kind,
        status=row.status,
        summary=_job_summary(row.kind, progress, result),
        progress=_safe_payload(progress),
        result_summary=_compact_result(result),
        created_at=_as_utc(row.created_at),
        updated_at=_as_utc(row.updated_at),
        completed_at=_as_utc(row.completed_at),
    )


def _long_task_job(row: dict[str, Any]) -> RuntimeJob:
    task = str(row.get("task") or row.get("task_type") or "Long task")
    return RuntimeJob(
        job_id=str(row.get("task_id") or ""),
        owner_id=str(row.get("owner_id") or ""),
        companion_id=_str_or_none(row.get("companion_id")),
        conversation_id=_str_or_none(row.get("conversation_id")),
        turn_id=_str_or_none(row.get("turn_id")),
        provider=str(row.get("provider") or "mementos"),
        kind=str(row.get("task_type") or "long_task"),
        status=str(row.get("status") or "unknown"),
        summary=_redact_text(task, keep=64),
        progress=_safe_payload({"summary": row.get("progress_summary"), "external_status": row.get("external_status")}),
        result_summary=_redact_text(str(row.get("result_tts_summary") or row.get("result_text") or ""), keep=80),
        created_at=_parse_dt(row.get("created_at")),
        updated_at=_parse_dt(row.get("updated_at")),
        completed_at=_parse_dt(row.get("completed_at")),
    )


def _dedupe_jobs(jobs: list[RuntimeJob]) -> list[RuntimeJob]:
    out: dict[str, RuntimeJob] = {}
    for job in sorted(
        jobs,
        key=lambda item: _as_utc(item.updated_at or item.created_at) or datetime.min.replace(tzinfo=UTC),
        reverse=True,
    ):
        out.setdefault(job.job_id, job)
    return list(out.values())


def _events_from_data(rows: list[Any]) -> list[RuntimeEvent]:
    events = []
    for row in rows:
        # Prefer the persisted classification columns (Phase 1); fall back to the
        # legacy string heuristics only for rows written before they existed.
        source = getattr(row, "source", None) or _event_source(row.event_type)
        severity = getattr(row, "severity", None) or (
            "warn" if "revoked" in row.event_type or "cancel" in row.event_type else "info"
        )
        outcome = getattr(row, "outcome", None) or "success"
        raw_payload = row.payload_json or {}
        payload = _safe_payload(raw_payload)
        device_id = (
            row.subject_id
            if row.subject_type == "device"
            else _str_or_none(raw_payload.get("device_id"))
        )
        events.append(
            RuntimeEvent(
                event_id=row.event_id,
                ts=_as_utc(getattr(row, "occurred_at", None) or row.created_at) or datetime.now(UTC),
                source=source,
                type=row.event_type,
                severity=severity,
                outcome=outcome,
                trace_id=getattr(row, "trace_id", None),
                owner_id=row.owner_id,
                companion_id=getattr(row, "companion_id", None),
                device_id=device_id,
                conversation_id=_str_or_none(raw_payload.get("conversation_id")),
                job_id=row.subject_id if row.subject_type == "job" else None,
                turn_id=row.subject_id if row.subject_type == "turn" else None,
                summary=_runtime_event_summary(
                    row.event_type,
                    row.subject_type,
                    row.subject_id,
                    raw_payload,
                ),
                payload=payload,
            )
        )
    return events


def _events_from_turns(turns: list[dict[str, Any]]) -> list[RuntimeEvent]:
    out = []
    for row in turns:
        turn = _turn(row)
        out.append(
            RuntimeEvent(
                event_id=f"turn-{turn.turn_id}",
                ts=turn.started_at or datetime.now(UTC),
                source="agent",
                type="agent.turn.observed",
                trace_id=turn.trace_id,
                owner_id=turn.owner_id,
                companion_id=turn.companion_id,
                device_id=turn.device_id,
                conversation_id=turn.conversation_id,
                turn_id=turn.turn_id,
                severity="error" if turn.status in {"errored", "failed"} else "info",
                summary=f"Turn {turn.status}: {turn.memory_hits} memory hit(s), {len(turn.tool_names)} tool(s)",
                payload={
                    "status": turn.status,
                    "latency_ms": turn.latency_ms,
                    "tool_names": turn.tool_names,
                    "privacy_mode": turn.privacy_mode,
                },
            )
        )
    return out


def _events_from_jobs(jobs: list[RuntimeJob]) -> list[RuntimeEvent]:
    out = []
    for job in jobs:
        out.append(
            RuntimeEvent(
                event_id=f"job-{job.job_id}",
                ts=_as_utc(job.updated_at or job.created_at) or datetime.now(UTC),
                source="agent",
                type="task.job.updated",
                owner_id=job.owner_id,
                companion_id=job.companion_id,
                conversation_id=job.conversation_id,
                turn_id=job.turn_id,
                job_id=job.job_id,
                severity="error" if job.status in {"failed", "errored"} else "info",
                summary=f"{job.provider}:{job.kind} -> {job.status}",
                payload={"summary": job.summary, "result_summary": job.result_summary},
            )
        )
    return out


# (token match, ledger kind, privacy level, raw-retention policy)
_PERMISSION_KINDS: list[tuple[tuple[str, ...], str, str, str]] = [
    (("camera", "photo", "vision"), "camera.take_photo", "sensitive", "not_stored"),
    (("room.join",), "room.join", "operation", "n/a"),
    (("identify",), "device.identify", "operation", "n/a"),
    (("volume",), "device.volume", "operation", "n/a"),
    (("brightness",), "device.brightness", "operation", "n/a"),
    (("command",), "device.command", "operation", "n/a"),
]


def _permission_ledger(events: list[RuntimeEvent]) -> list[PermissionLedgerItem]:
    """Surface high-sensitivity capability calls for audit. Summaries are already
    redacted upstream, so this only reshapes — it never re-reads raw payloads."""
    out: list[PermissionLedgerItem] = []
    for event in events:
        blob = f"{event.type} {event.summary}".lower()
        match = next((m for m in _PERMISSION_KINDS if any(tok in blob for tok in m[0])), None)
        if match is None:
            continue
        _, kind, privacy_level, retention = match
        status = event.summary.rsplit("->", 1)[-1].strip() if "->" in event.summary else ""
        out.append(
            PermissionLedgerItem(
                ts=event.ts,
                kind=kind,
                device_id=event.device_id,
                status=status,
                privacy_level=privacy_level,
                raw_retention=retention,
                summary=event.summary,
            )
        )
    return out[:20]


def _evidence_chains(
    *,
    companion: Any | None,
    devices: list[RuntimeDevice],
    memory: RuntimeMemory,
    jobs: list[RuntimeJob],
    ledger: list[PermissionLedgerItem],
) -> list[EvidenceChain]:
    """Derive the three demo proof-chains. Confidence = done/total; a chain is
    only 'proven' when every step has real backing — mock is never marked live."""
    online = [d for d in devices if d.online]
    has_camera = any(
        "camera.snapshot" in d.capabilities or "camera" in d.kind.lower()
        for d in devices
    )
    camera_grant = next((i for i in ledger if i.kind == "camera.take_photo"), None)
    active_jobs = [j for j in jobs if (j.status or "").lower() in {"running", "queued", "accepted", "pending", "active"}]
    done_jobs = [j for j in jobs if (j.status or "").lower() in {"succeeded", "done", "completed"}]

    cross_body = [
        EvidenceStep(key="bodies", label="多身体在线", done=len(devices) >= 2, detail=f"{len(online)}/{len(devices)} 在线"),
        EvidenceStep(key="identity", label="同一身份与记忆域", done=companion is not None and memory.realms_total > 0),
        EvidenceStep(key="write", label="记忆写入", done=memory.last_write_disposition is not None),
        EvidenceStep(key="recall", label="跨身体召回命中", done=memory.last_recall_hits > 0, detail=f"{memory.last_recall_hits} hit"),
    ]
    vision = [
        EvidenceStep(key="capability", label="视觉身体", done=has_camera),
        EvidenceStep(key="authorized", label="授权调用摄像头", done=camera_grant is not None, detail=camera_grant.summary if camera_grant else ""),
        EvidenceStep(key="retention", label="仅摘要 · 不留原图", done=camera_grant is not None),
    ]
    coworker = [
        EvidenceStep(key="delegate", label="任务交办", done=bool(jobs)),
        EvidenceStep(key="running", label="后台执行", done=bool(active_jobs) or bool(done_jobs)),
        EvidenceStep(key="artifact", label="产物完成", done=any(j.result_summary for j in done_jobs)),
        EvidenceStep(key="report", label="回报 / 落库", done=bool(done_jobs)),
    ]

    def _chain(key: str, title: str, claim: str, steps: list[EvidenceStep]) -> EvidenceChain:
        done = sum(1 for s in steps if s.done)
        total = len(steps)
        status = "proven" if done == total else "partial" if done else "pending"
        return EvidenceChain(key=key, title=title, claim=claim, status=status, confidence=round(done / total * 100), steps=steps)

    return [
        _chain("cross_body_memory", "跨身体记忆", "同一伙伴的身份与记忆不绑定任何单一硬件。", cross_body),
        _chain("vision_permission", "视觉授权", "摄像头是受权限管理的视觉身体，默认只留摘要、不扩散原图。", vision),
        _chain("coworker_task", "Coworker 任务", "前台对话可把上下文交给后台数字员工并回报产物。", coworker),
    ]


def _trace_spans(turn: RuntimeTurn | None) -> list[RuntimeTraceSpan]:
    """Structured spans for one observed voice turn.

    They are derived from already-safe stages and tool names. There is no extra
    Agent fetch (no N+1) and no text payload.
    """
    if turn is None:
        return []
    spans: list[RuntimeTraceSpan] = []
    for stage in turn.stages:
        key = str(stage.get("key") or "stage")
        spans.append(
            RuntimeTraceSpan(
                span_id=f"{turn.turn_id}:{key}",
                turn_id=turn.turn_id,
                name=str(stage.get("label") or key),
                kind=key,
                status=str(stage.get("status") or "done"),
                latency_ms=_int_or_none(stage.get("latency_ms")),
                detail=str(stage.get("detail") or ""),
            )
        )
    for idx, tool in enumerate(turn.tool_names):
        spans.append(
            RuntimeTraceSpan(
                span_id=f"{turn.turn_id}:tool:{idx}",
                turn_id=turn.turn_id,
                name=str(tool),
                kind="tool",
                status="done",
                detail="tool call",
            )
        )
    return spans


def _experience(
    *,
    owner: Any,
    companion: Any | None,
    devices: list[RuntimeDevice],
    services: list[RuntimeService],
    activities: list[RuntimeActivity],
    primary_voice_turn: RuntimeTurn | None,
    memory: RuntimeMemory,
    jobs: list[RuntimeJob],
    recent_events: list[RuntimeEvent],
    source_status: list[SourceStatus],
) -> RuntimeExperience:
    online_devices = [device for device in devices if device.online]
    companion_name = getattr(companion, "display_name", "") or getattr(companion, "companion_id", "") or "伙伴"
    owner_name = getattr(owner, "display_name", "") or getattr(owner, "owner_id", "") or "当前用户"
    latest_event = recent_events[0] if recent_events else None
    turn_event = _latest_event(recent_events, "turn")
    running_jobs = [job for job in jobs if job.status not in {"ok", "done", "succeeded", "completed", "failed", "errored"}]
    degraded = [status for status in source_status if not status.ok]
    active_activities = [activity for activity in activities if _activity_is_active(activity.status)]
    completion = _experience_completion(devices, services, activities, memory, jobs, recent_events)
    system_state = "active" if active_activities else ("working" if running_jobs else ("watching" if latest_event else "standby"))

    if primary_voice_turn:
        headline = f"{companion_name} 正在处理一次来自身体的交互"
        subheadline = "身份、记忆、工具和设备状态正在被串成一条可见链路。"
    elif active_activities:
        headline = f"{owner_name} 有 {len(active_activities)} 条运行活动"
        subheadline = "Guard、设备事件、指令和后台任务各自保留独立路径，不依赖语音轮次。"
    elif running_jobs:
        headline = f"{companion_name} 有后台任务正在推进"
        subheadline = "前台可以继续交流，后台任务会独立执行并在完成后回到同一个 companion。"
    elif turn_event:
        headline = f"{companion_name} 最近完成了一次跨系统交互"
        subheadline = "身体、身份、记忆、行动和权限状态已经汇入当前运行视图。"
    elif online_devices:
        headline = f"{companion_name} 已连接 {len(online_devices)} 个身体"
        subheadline = "这些身体共享同一个身份、记忆和权限边界，不是彼此孤立的聊天入口。"
    else:
        headline = f"{companion_name} 的 Agent OS 待命中"
        subheadline = "连接硬件或发起一次对话后，这里会显示系统如何调度身体、记忆和任务。"

    plain_summary = (
        f"{owner_name} 面前看到的是一个 companion；这块屏幕展示的是背后的 Agent OS："
        f"{len(devices)} 个身体、{memory.realms_total} 个记忆空间、{len(services)} 个运行服务，以及最近 {len(recent_events)} 条安全摘要事件。"
    )
    if degraded:
        next_best_action = f"有 {len(degraded)} 个信息源暂时不可用；当前链路仍会持续显示已接入的实时状态。"
    elif not devices:
        next_best_action = "先绑定或启动一个硬件身体，大屏会立刻出现身体拓扑。"
    elif not active_activities:
        next_best_action = "对 2.06、BOX-3 或 Web body 说一句话，观察身份、记忆和任务链路如何亮起。"
    else:
        next_best_action = "继续观察这次交互：回应完成后，记忆写入和任务状态会回到这里。"

    return RuntimeExperience(
        headline=headline,
        subheadline=subheadline,
        plain_summary=plain_summary,
        system_state=system_state,
        completion=completion,
        storyline=_storyline(devices, primary_voice_turn, memory, jobs, recent_events),
        lanes=_experience_lanes(devices, services, primary_voice_turn, memory, jobs, recent_events, degraded),
        capability_cards=_capability_cards(devices, services, primary_voice_turn, memory, jobs, recent_events),
        next_best_action=next_best_action,
    )


def _storyline(
    devices: list[RuntimeDevice],
    primary_voice_turn: RuntimeTurn | None,
    memory: RuntimeMemory,
    jobs: list[RuntimeJob],
    events: list[RuntimeEvent],
) -> list[RuntimeStoryStep]:
    latest_device = next((device for device in devices if device.online), devices[0] if devices else None)
    camera_event = next((event for event in events if _is_sensor_event(event)), None)
    turn_event = _latest_event(events, "turn")
    job = jobs[0] if jobs else None
    latest_event = events[0] if events else None
    return [
        RuntimeStoryStep(
            key="body",
            title="身体接入",
            detail=(
                f"{latest_device.name or latest_device.device_id} 可以作为输入/输出身体"
                if latest_device
                else "还没有检测到可展示的身体"
            ),
            status="done" if latest_device else "pending",
            source="hub",
            event_origin="live",
            ts=latest_device.last_seen_at if latest_device else None,
        ),
        RuntimeStoryStep(
            key="identity",
            title="身份归一",
            detail="无论从哪个身体进入，都会解析到同一个 owner、companion 和记忆空间。",
            status="done",
            source="data",
            ts=latest_event.ts if latest_event else None,
        ),
        RuntimeStoryStep(
            key="turn",
            title="智能体处理",
            detail=(
                f"最近一次交互状态：{_friendly_status(primary_voice_turn.status)}"
                if primary_voice_turn
                else (
                    f"最近一次交互事件：{turn_event.summary}"
                    if turn_event
                    else "等待一次真实对话或 PTT 输入"
                )
            ),
            status=_stage_status(primary_voice_turn.status) if primary_voice_turn else ("done" if turn_event else "pending"),
            source="agent",
            ts=primary_voice_turn.started_at if primary_voice_turn else (turn_event.ts if turn_event else None),
        ),
        RuntimeStoryStep(
            key="memory",
            title="记忆参与",
            detail=f"本轮召回 {memory.last_recall_hits} 条记忆，写入策略：{_friendly_memory_write(memory.last_write_disposition)}。",
            status="done" if memory.realms_total else "pending",
            source="memory",
            ts=latest_event.ts if latest_event else None,
        ),
        RuntimeStoryStep(
            key="tools",
            title="行动调度",
            detail=(
                f"{job.provider}:{job.kind} 最近更新，状态 {_friendly_status(job.status)}。"
                if job
                else "整理资料、拍照、控制设备等行动会进入这里。"
            ),
            status=_stage_status(job.status) if job else "pending",
            source="agent",
            ts=(job.updated_at or job.created_at) if job else None,
        ),
        RuntimeStoryStep(
            key="permission",
            title="权限可见",
            detail=(
                f"最近一次设备/传感器事件：{camera_event.summary}"
                if camera_event
                else "摄像头、麦克风和设备控制默认只展示授权与摘要。"
            ),
            status="done" if camera_event else "pending",
            source="hub",
            event_origin="live",
            ts=camera_event.ts if camera_event else None,
        ),
    ]


def _experience_lanes(
    devices: list[RuntimeDevice],
    services: list[RuntimeService],
    primary_voice_turn: RuntimeTurn | None,
    memory: RuntimeMemory,
    jobs: list[RuntimeJob],
    events: list[RuntimeEvent],
    degraded: list[SourceStatus],
) -> list[RuntimeLane]:
    online_devices = [device for device in devices if device.online]
    sensor_events = [event for event in events if _is_sensor_event(event)]
    service_online = sum(1 for service in services if service.online)
    return [
        RuntimeLane(
            key="body",
            title="身体网络",
            headline=f"{len(online_devices)}/{len(devices)} 个身体在线",
            detail="2.06、BOX-3、摄像头板和 Web body 都可以成为同一个 companion 的入口。",
            status="done" if online_devices else ("idle" if devices else "pending"),
            items=[
                RuntimeLaneItem(
                    label=device.role,
                    value=device.name or device.device_id,
                    status=_device_lane_status(device),
                    detail=_device_lane_detail(device),
                )
                for device in devices[:5]
            ],
        ),
        RuntimeLane(
            key="turn",
            title="一次交互",
            headline=(
                f"最近一次交互：{_friendly_status(primary_voice_turn.status)}"
                if primary_voice_turn
                else "等待用户开口"
            ),
            detail="这里展示从身体输入到智能体回应的关键步骤，不展示私密原文。",
            status=_stage_status(primary_voice_turn.status) if primary_voice_turn else "pending",
            items=[
                RuntimeLaneItem(
                    label=str(stage.get("label") or ""),
                    value=_friendly_status(stage.get("status")),
                    status=str(stage.get("status") or "pending"),
                    detail=(f"{stage.get('latency_ms')} ms" if stage.get("latency_ms") is not None else ""),
                )
                for stage in (primary_voice_turn.stages if primary_voice_turn else [])
            ],
        ),
        RuntimeLane(
            key="memory",
            title="记忆",
            headline=f"召回 {memory.last_recall_hits} 条，{memory.realms_total} 个记忆空间",
            detail="记忆用于让 companion 跨身体保持连续，但默认只显示数量、策略和摘要。",
            status="done" if memory.realms_total else "pending",
            items=[
                RuntimeLaneItem(label="活跃记忆空间", value=memory.active_realm_id or "未配置", status="done" if memory.active_realm_id else "pending"),
                RuntimeLaneItem(label="写入策略", value=_friendly_memory_write(memory.last_write_disposition), status="done" if memory.fanout_allowed else "idle"),
                RuntimeLaneItem(label="记忆后台", value=f"{memory.runners_online}/{memory.runners_total} 在线", status="done" if memory.runners_online else "idle"),
            ],
        ),
        RuntimeLane(
            key="task",
            title="行动调度",
            headline=f"{len(jobs)} 个任务可见" if jobs else "还没有后台任务",
            detail="整理、查询、生成和委托类行动会在这里持续推进。",
            status="running" if any(_stage_status(job.status) == "running" for job in jobs) else ("done" if jobs else "pending"),
            items=[
                RuntimeLaneItem(
                    label=job.kind,
                    value=_friendly_status(job.status),
                    status=_stage_status(job.status),
                    detail=job.summary,
                )
                for job in jobs[:4]
            ],
        ),
        RuntimeLane(
            key="permission",
            title="权限与传感器",
            headline=f"{len(sensor_events)} 条设备/传感器摘要" if sensor_events else "高敏能力默认可审计",
            detail="摄像头、麦克风和设备控制不会把原始内容直接摊在大屏上。",
            status="done" if sensor_events else "pending",
            items=[
                RuntimeLaneItem(
                    label=_friendly_event_type(event.type),
                    value=_friendly_source(event.source),
                    status=_stage_status(event.severity),
                    detail=event.summary,
                )
                for event in sensor_events[:4]
            ],
        ),
        RuntimeLane(
            key="runtime",
            title="运行底座",
            headline=f"{service_online}/{len(services)} 个服务在线",
            detail="这些服务共同支撑身份解析、设备连接、Agent 推理、记忆和任务。",
            status="done" if not degraded else "degraded",
            items=[
                RuntimeLaneItem(
                    label=_friendly_service_name(service.service_id, service.name),
                    value="在线" if service.online else ("未配置" if not service.checked else "离线"),
                    status="done" if service.online else "degraded",
                    detail=service.detail,
                )
                for service in services[:7]
            ],
        ),
    ]


def _capability_cards(
    devices: list[RuntimeDevice],
    services: list[RuntimeService],
    primary_voice_turn: RuntimeTurn | None,
    memory: RuntimeMemory,
    jobs: list[RuntimeJob],
    events: list[RuntimeEvent],
) -> list[RuntimeCapabilityCard]:
    caps = {cap for device in devices for cap in device.capabilities}
    return [
        RuntimeCapabilityCard(
            key="multi_body",
            title="多身体",
            metric=f"{sum(1 for device in devices if device.online)}/{len(devices)}",
            status="done" if devices else "pending",
            detail="同一个 companion 可以通过不同硬件出现。",
        ),
        RuntimeCapabilityCard(
            key="memory",
            title="主权记忆",
            metric=str(memory.last_recall_hits),
            status="done" if memory.realms_total else "pending",
            detail="记忆跟随 owner，而不是绑死在某台设备。",
        ),
        RuntimeCapabilityCard(
            key="voice",
            title="语音链路",
            metric="已接入" if primary_voice_turn else "待触发",
            status="done" if primary_voice_turn else "pending",
            detail="语音输入会进入身份解析、Agent 和记忆链路。",
        ),
        RuntimeCapabilityCard(
            key="vision",
            title="视觉/传感器",
            metric="可见" if ("camera.snapshot" in caps or any(_is_sensor_event(event) for event in events)) else "待接入",
            status="done" if "camera.snapshot" in caps else "pending",
            detail="高敏输入默认展示授权和摘要，不展示原始图像。",
        ),
        RuntimeCapabilityCard(
            key="tasks",
            title="后台任务",
            metric=str(len(jobs)),
            status="done" if jobs else "pending",
            detail="复杂任务可以离开当前对话独立执行。",
        ),
        RuntimeCapabilityCard(
            key="services",
            title="运行底座",
            metric=f"{sum(1 for svc in services if svc.online)}/{len(services)}",
            status="done" if services and all(svc.online or not svc.checked for svc in services) else "degraded",
            detail="Hub、Agent、Memory、Admin 等服务共同构成 Agent OS。",
        ),
    ]


def _default_companion(companions: list[Any], default_companion_id: str | None) -> Any | None:
    """The Companion the Owner's pointer names, or nothing.

    It used to be "the first row whose status is active", falling back to the
    first row. Two things were wrong with that: the attribute it read does not
    exist on a Companion row (so the loop never matched), and picking a row at
    all made this a *second* place deciding which Companion is the default. It
    happened to be right while there was one Companion, which is the worst way
    for it to be wrong.

    Returning None when the pointer names nothing is deliberate: an Owner with
    no default is a real state, and a cockpit that showed one anyway would
    disagree with every other surface.
    """

    if not default_companion_id:
        return None
    for row in companions:
        if getattr(row, "companion_id", None) == default_companion_id:
            return row
    return None


def _runtime_companion(row: Any) -> RuntimeCompanion:
    return RuntimeCompanion(
        companion_id=getattr(row, "companion_id", "") or "",
        display_name=getattr(row, "display_name", "") or "",
        kind=getattr(row, "kind", "") or "",
        lifecycle_state=getattr(row, "lifecycle_state", "") or "",
        genome_id=getattr(row, "current_genome_id", None),
        memory_realm_id=getattr(row, "default_memory_realm_id", None),
    )


def _event_summary(event_type: str, subject_type: str, subject_id: str) -> str:
    readable = event_type.replace(".", " ")
    return f"{readable} · {subject_type}:{subject_id}"


def _runtime_event_summary(
    event_type: str,
    subject_type: str,
    subject_id: str,
    payload: dict[str, Any],
) -> str:
    if event_type == "channel.turn.phase_changed":
        phase = str(payload.get("phase") or "unknown")
        reason = str(payload.get("transition_event") or "")
        return f"Turn phase · {phase}{f' · {reason}' if reason else ''}"
    if event_type == "channel.turn.milestone":
        milestone = str(payload.get("milestone") or "unknown")
        return f"Turn milestone · {milestone}"
    if event_type in _CHANNEL_TERMINAL_TYPES:
        status = str(payload.get("status") or event_type.rsplit(".", 1)[-1])
        reason = str(payload.get("terminal_reason") or "")
        return f"Turn {status}{f' · {reason}' if reason else ''}"
    return _event_summary(event_type, subject_type, subject_id)


def _event_source(event_type: str) -> str:
    lowered = event_type.lower()
    if "memory" in lowered or "fanout" in lowered or "steward" in lowered:
        return "memory"
    if any(token in lowered for token in ("audio", "stt", "tts", "livekit", "room.")):
        return "channel"
    if any(token in lowered for token in ("device", "camera", "sensor", "command")):
        return "hub"
    if any(token in lowered for token in ("turn", "tool", "job", "task")):
        return "agent"
    return "data"


def _friendly_status(value: Any) -> str:
    status = str(value or "").lower()
    if status in {"ok", "done", "succeeded", "completed", "success"}:
        return "已完成"
    if status in {"running", "processing", "in_progress"}:
        return "进行中"
    if status in {"pending", "queued", "created"}:
        return "排队中"
    if status in {"failed", "error", "errored", "timeout"}:
        return "需要关注"
    if status in {"degraded", "warn", "warning"}:
        return "部分可用"
    if status == "info":
        return "有新信号"
    return str(value or "等待中")


def _friendly_memory_write(value: Any) -> str:
    disposition = str(value or "").lower()
    if disposition in {"write", "written", "accepted", "fanout"}:
        return "允许写入"
    if disposition in {"ignore", "ignored", "skip", "skipped"}:
        return "本轮不写入"
    if disposition in {"pending", "queued"}:
        return "等待判断"
    if disposition in {"denied", "blocked", "private"}:
        return "隐私保护"
    return str(value or "等待判断")


def _friendly_capability(value: str) -> str:
    mapping = {
        "voice": "听说",
        "speaker": "播放",
        "display": "显示",
        "sensor": "传感",
        "ptt": "按键说话",
        "touch": "触控",
        "camera.snapshot": "拍照",
        "vision": "视觉",
        "control": "控制",
    }
    return mapping.get(value, value)


def _friendly_source(value: str) -> str:
    return {
        "hub": "身体/设备",
        "channel": "语音通道",
        "agent": "智能体引擎",
        "memory": "记忆系统",
        "data": "事实账本",
        "admin": "控制台",
            "mission_control": "飞控台",
    }.get(value, value)


def _friendly_event_type(value: str) -> str:
    lowered = value.lower()
    if "camera" in lowered:
        return "视觉请求"
    if "command" in lowered:
        return "设备控制"
    if "probe" in lowered or "presence" in lowered:
        return "设备在线探测"
    if "memory" in lowered or "fanout" in lowered:
        return "记忆同步"
    if "turn" in lowered:
        return "一次对话"
    if "job" in lowered or "task" in lowered:
        return "后台任务"
    return value.replace(".", " ")


def _friendly_service_name(service_id: str, name: str) -> str:
    return {
        "admin": "控制台",
        "agent": "智能体引擎",
        "hub": "身体中枢",
        "memory": "记忆系统",
        "channel": "语音通道",
        "mementos": "后台协作者",
        "client-web": "Web 身体",
    }.get(service_id, name or service_id)


def _is_sensor_event(event: RuntimeEvent) -> bool:
    blob = f"{event.type} {event.summary} {event.device_id or ''}".lower()
    return any(token in blob for token in ("camera", "sensor", "photo", "vision", "command", "device.command"))


def _latest_event(events: list[RuntimeEvent], token: str) -> RuntimeEvent | None:
    token = token.lower()
    for event in events:
        if token in f"{event.type} {event.summary} {event.subject if hasattr(event, 'subject') else ''}".lower():
            return event
    return None


def _experience_completion(
    devices: list[RuntimeDevice],
    services: list[RuntimeService],
    activities: list[RuntimeActivity],
    memory: RuntimeMemory,
    jobs: list[RuntimeJob],
    events: list[RuntimeEvent],
) -> int:
    checks = [
        bool(devices),
        any(device.online for device in devices),
        bool(services) and any(service.online for service in services),
        any(_activity_is_active(activity.status) for activity in activities),
        memory.realms_total > 0,
        memory.last_recall_hits > 0 or memory.last_write_disposition is not None,
        bool(jobs),
        any(_is_sensor_event(event) for event in events),
        any(event.source == "hub" for event in events),
        any(event.source in {"agent", "memory", "channel"} for event in events),
    ]
    return round((sum(1 for item in checks if item) / len(checks)) * 100)


def _job_summary(kind: str, progress: dict[str, Any], result: dict[str, Any]) -> str:
    for key in ("summary", "progress_summary", "message", "title"):
        if progress.get(key):
            return _redact_text(str(progress[key]), keep=80)
    return _compact_result(result) or kind


def _compact_result(result: dict[str, Any]) -> str:
    if not result:
        return ""
    for key in ("summary", "result_text", "title", "path", "artifact"):
        if result.get(key):
            return _redact_text(str(result[key]), keep=80)
    return f"{len(result)} result field(s)"


def _health_url(service: Any) -> str:
    health = str(getattr(service, "health", "") or "")
    if not health:
        return ""
    if health.startswith(("http://", "https://")):
        return health
    base = str(getattr(service, "base_url", "") or "").rstrip("/")
    return f"{base}{health}" if base else ""


def _safe_payload(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            key_s = str(key)
            if key_s.lower() in _TEXT_KEYS:
                out[key_s] = _redact_text(str(item))
            else:
                out[key_s] = _safe_payload(item)
        return out
    if isinstance(value, list):
        return [_safe_payload(item) for item in value[:20]]
    if isinstance(value, str) and len(value) > 160:
        return _redact_text(value)
    return value


def _redact_text(text: str, *, keep: int = 40) -> str:
    if not text:
        return ""
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]
    compact = " ".join(text.split())
    preview = compact[:keep]
    suffix = "..." if len(compact) > keep else ""
    return f"{preview}{suffix} [redacted:{digest}]"


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _as_utc(value)
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return _as_utc(parsed)
    except ValueError:
        return None


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _status(source: str, ok: bool, started: float, detail: str = "") -> SourceStatus:
    return SourceStatus(
        source=source,
        ok=ok,
        detail=detail,
        latency_ms=round((time.perf_counter() - started) * 1000, 1),
    )


def _coalesce_statuses(statuses: list[SourceStatus]) -> list[SourceStatus]:
    out: dict[str, SourceStatus] = {}
    for status in statuses:
        out[status.source] = status
    return list(out.values())


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _dict_or_empty(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None
