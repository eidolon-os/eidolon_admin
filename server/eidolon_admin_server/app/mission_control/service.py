"""Runtime aggregation for Mission Control.

This module is intentionally read-only. It composes existing admin, hub,
eidolon_data, agent, and memory management surfaces into a prompt-safe runtime
view for a large-screen observatory.
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

from eidolon_data import DataStore

from .schemas import (
    RuntimeCapabilityCard,
    RuntimeCompanion,
    RuntimeDevice,
    RuntimeEvent,
    RuntimeExperience,
    RuntimeJob,
    RuntimeLane,
    RuntimeLaneItem,
    RuntimeMemory,
    RuntimeOwner,
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
    "prompt_markdown",
    "image",
    "image_url",
    "raw_image",
    "audio",
}


async def build_snapshot(request: Request, owner_id: str | None = None) -> RuntimeSnapshot:
    started = time.perf_counter()
    generated_at = datetime.now(UTC)
    statuses: list[SourceStatus] = []
    store = _store(request)
    if store is None:
        return RuntimeSnapshot(
            generated_at=generated_at,
            source_status=[
                SourceStatus(source="data", ok=False, detail="eidolon_data unavailable")
            ],
        )

    owner = await _select_owner(store, owner_id)
    if owner is None:
        return RuntimeSnapshot(
            generated_at=generated_at,
            source_status=[SourceStatus(source="data", ok=True, detail="no owners")],
        )
    owner_id = owner.owner_id

    companions, devices, conversations, memory_realms, jobs, data_events = await asyncio.gather(
        _safe(statuses, "data.companions", store.companions.list_for_owner(owner_id), []),
        _safe(statuses, "data.devices", store.devices.list_devices_for_owner(owner_id), []),
        _safe(statuses, "data.conversations", store.conversations.list_for_owner(owner_id, limit=20), []),
        _safe(statuses, "data.memory", store.memory_repo.list_realms_for_owner(owner_id), []),
        _safe(statuses, "data.jobs", store.jobs.list_for_owner(owner_id, limit=20), []),
        _safe(statuses, "data.events", store.events.list_for_owner(owner_id, limit=40), []),
    )
    statuses.append(_status("data", True, started))

    companion = _default_companion(companions)
    hub_devices = await _hub_devices(request, statuses)
    runtime_devices = _merge_devices(devices, hub_devices)
    services, service_statuses = await _services(request)
    statuses.extend(service_statuses)

    turns = await _agent_turns(request, owner_id, statuses)
    long_tasks = await _agent_long_tasks(request, owner_id, statuses)
    memory = await _memory_summary(request, memory_realms, turns, statuses)

    runtime_jobs = [_job(row) for row in jobs]
    runtime_jobs.extend(_long_task_job(row) for row in long_tasks)
    runtime_jobs = _dedupe_jobs(runtime_jobs)[:12]

    recent_events = _events_from_data(data_events)
    recent_events.extend(_events_from_turns(turns[:5]))
    recent_events.extend(_events_from_jobs(runtime_jobs[:5]))
    recent_events = sorted(
        recent_events,
        key=lambda ev: _as_utc(ev.ts) or datetime.min.replace(tzinfo=UTC),
        reverse=True,
    )[:60]

    runtime_turns = [_turn(row) for row in turns]
    active_turn = _active_turn(runtime_turns)

    source_status = _coalesce_statuses(statuses)
    experience = _experience(
        owner=owner,
        companion=companion,
        devices=runtime_devices,
        services=services,
        active_turn=active_turn,
        memory=memory,
        jobs=runtime_jobs,
        recent_events=recent_events,
        source_status=source_status,
    )

    return RuntimeSnapshot(
        generated_at=generated_at,
        owner=RuntimeOwner(
            owner_id=owner.owner_id,
            display_name=owner.display_name,
            kind=owner.kind,
            status=owner.status,
        ),
        companion=RuntimeCompanion(
            companion_id=getattr(companion, "companion_id", "") or "",
            display_name=getattr(companion, "display_name", "") or "",
            kind=getattr(companion, "kind", "") or "",
            status=getattr(companion, "status", "") or "",
            genome_id=getattr(companion, "current_genome_id", None),
            memory_realm_id=getattr(companion, "default_memory_realm_id", None),
        ) if companion is not None else None,
        devices=runtime_devices,
        services=services,
        active_turn=active_turn,
        recent_turns=runtime_turns[:12],
        memory=memory,
        jobs=runtime_jobs,
        recent_events=recent_events,
        source_status=source_status,
        experience=experience,
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


def _store(request: Request) -> DataStore | None:
    return getattr(request.app.state, "data_store", None)


async def _select_owner(store: DataStore, owner_id: str | None) -> Any | None:
    if owner_id:
        return await store.owners.get(owner_id)
    owners = await store.owners.list()
    active = [row for row in owners if getattr(row, "status", "") == "active"]
    return (active or owners)[0] if owners else None


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

    async def _probe(svc: Any) -> RuntimeService:
        started = time.perf_counter()
        url = _health_url(svc)
        if not url:
            return RuntimeService(
                service_id=svc.id,
                name=svc.name,
                online=False,
                checked=False,
                detail="no health probe",
            )
        try:
            resp = await http_client.get(url, timeout=1.2, headers={"Connection": "close"})
            return RuntimeService(
                service_id=svc.id,
                name=svc.name,
                online=resp.status_code < 500,
                checked=True,
                latency_ms=round((time.perf_counter() - started) * 1000, 1),
                detail=f"HTTP {resp.status_code}",
            )
        except Exception as exc:  # noqa: BLE001
            return RuntimeService(
                service_id=svc.id,
                name=svc.name,
                online=False,
                checked=True,
                latency_ms=round((time.perf_counter() - started) * 1000, 1),
                detail=str(exc),
            )

    rows = await asyncio.gather(*(_probe(svc) for svc in registry.services))
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


def _merge_devices(data_devices: list[Any], hub_devices: list[Any]) -> list[RuntimeDevice]:
    by_id: dict[str, dict[str, Any]] = {}
    for row in data_devices:
        by_id[row.device_id] = {"data": row, "hub": None}
    for row in hub_devices:
        by_id.setdefault(row.device_id, {"data": None, "hub": None})["hub"] = row

    devices = []
    for device_id, parts in by_id.items():
        data = parts["data"]
        hub = parts["hub"]
        name = getattr(data, "name", "") or getattr(hub, "name", "") or device_id
        kind = getattr(data, "kind", "") or getattr(hub, "kind", "") or "unknown"
        status = getattr(hub, "status", "") or getattr(data, "status", "") or "offline"
        last_seen = _as_utc(getattr(hub, "last_seen", None) or getattr(data, "last_seen_at", None))
        capabilities = _capabilities(data, hub)
        devices.append(
            RuntimeDevice(
                device_id=device_id,
                name=name,
                role=_device_role(device_id, name, kind, capabilities),
                kind=kind,
                status=status,
                online=status == "online",
                approved=bool(getattr(hub, "approved", False) or getattr(data, "approved_at", None)),
                owner_id=getattr(data, "owner_id", None),
                companion_id=getattr(data, "bound_companion_id", None),
                interaction_mode=getattr(data, "interaction_mode", None),
                room_name=getattr(hub, "room_name", "") or ((getattr(data, "network_json", {}) or {}).get("room_name") or ""),
                participant_sid=getattr(hub, "participant_sid", ""),
                last_seen_at=last_seen,
                capabilities=capabilities,
                signals={
                    "missed_probes": getattr(hub, "missed_probes", 0),
                    "paired": bool(getattr(hub, "paired", False)),
                    "source": "hub+data" if data and hub else ("data" if data else "hub"),
                },
            )
        )
    return sorted(devices, key=lambda item: (not item.online, item.role, item.device_id))


def _capabilities(data: Any, hub: Any) -> list[str]:
    caps: set[str] = set()
    raw = getattr(data, "capabilities_json", None) or {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            if isinstance(value, bool) and value:
                caps.add(key)
            elif isinstance(value, list):
                caps.update(str(item) for item in value)
    kind = (getattr(data, "kind", "") or getattr(hub, "kind", "") or "").lower()
    name = (getattr(data, "name", "") or getattr(hub, "name", "") or "").lower()
    blob = f"{kind} {name}"
    if "box" in blob:
        caps.update({"voice", "speaker", "display", "sensor"})
    if "2.06" in blob or "amoled" in blob or "touch" in blob:
        caps.update({"ptt", "display", "touch", "voice"})
    if "camera" in blob or "vision" in blob or "atk" in blob:
        caps.update({"camera.snapshot", "vision", "sensor"})
    if not caps and "esp" in blob:
        caps.update({"voice", "control"})
    return sorted(caps)


def _device_role(device_id: str, name: str, kind: str, caps: list[str]) -> str:
    blob = f"{device_id} {name} {kind} {' '.join(caps)}".lower()
    if "2.06" in blob or "pocket" in blob or "ptt" in blob:
        return "Pocket PTT Controller"
    if "camera" in blob or "vision" in blob or "atk" in blob:
        return "Vision Node"
    if "box" in blob and ("dock" in blob or "sensor" in blob):
        return "Room Voice / Sensor Dock"
    if "box" in blob:
        return "Room Voice Node"
    if "web" in blob:
        return "Web Body"
    return "Body Node"


def _turn(row: dict[str, Any]) -> RuntimeTurn:
    obs = row.get("observability_summary") or {}
    memory = obs.get("memory") or {}
    tools = obs.get("tools") or {}
    latency = obs.get("latency") or {}
    stages = _turn_stages(row, obs)
    return RuntimeTurn(
        turn_id=str(row.get("turn_id") or ""),
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
            "label": f"小忆思考并回应：{_friendly_status(row.get('status'))}",
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


def _active_turn(turns: list[RuntimeTurn]) -> RuntimeTurn | None:
    for turn in turns:
        if turn.status not in {"ok", "done", "succeeded", "completed"}:
            return turn
    return turns[0] if turns else None


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
        events.append(
            RuntimeEvent(
                event_id=row.event_id,
                ts=_as_utc(row.created_at) or datetime.now(UTC),
                source=_event_source(row.event_type),
                type=row.event_type,
                owner_id=row.owner_id,
                device_id=row.subject_id if row.subject_type == "device" else None,
                job_id=row.subject_id if row.subject_type == "job" else None,
                turn_id=row.subject_id if row.subject_type == "turn" else None,
                severity="warn" if "revoked" in row.event_type or "cancel" in row.event_type else "info",
                summary=_event_summary(row.event_type, row.subject_type, row.subject_id),
                payload=_safe_payload(row.payload_json or {}),
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


def _experience(
    *,
    owner: Any,
    companion: Any | None,
    devices: list[RuntimeDevice],
    services: list[RuntimeService],
    active_turn: RuntimeTurn | None,
    memory: RuntimeMemory,
    jobs: list[RuntimeJob],
    recent_events: list[RuntimeEvent],
    source_status: list[SourceStatus],
) -> RuntimeExperience:
    online_devices = [device for device in devices if device.online]
    companion_name = getattr(companion, "display_name", "") or getattr(companion, "companion_id", "") or "小忆"
    owner_name = getattr(owner, "display_name", "") or getattr(owner, "owner_id", "") or "当前用户"
    latest_event = recent_events[0] if recent_events else None
    turn_event = _latest_event(recent_events, "turn")
    running_jobs = [job for job in jobs if job.status not in {"ok", "done", "succeeded", "completed", "failed", "errored"}]
    degraded = [status for status in source_status if not status.ok]
    online_services = [service for service in services if service.online]
    completion = _experience_completion(devices, services, active_turn, memory, jobs, recent_events)
    system_state = "active" if active_turn else ("working" if running_jobs else ("watching" if latest_event else "standby"))

    if active_turn:
        headline = f"{companion_name} 正在处理一次来自身体的交互"
        subheadline = "身份、记忆、工具和设备状态正在被串成一条可见链路。"
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
    elif not active_turn:
        next_best_action = "对 2.06、BOX-3 或 Web body 说一句话，观察身份、记忆和任务链路如何亮起。"
    else:
        next_best_action = "继续观察这次交互：回应完成后，记忆写入和任务状态会回到这里。"

    return RuntimeExperience(
        headline=headline,
        subheadline=subheadline,
        plain_summary=plain_summary,
        system_state=system_state,
        completion=completion,
        storyline=_storyline(devices, active_turn, memory, jobs, recent_events),
        lanes=_experience_lanes(devices, services, active_turn, memory, jobs, recent_events, degraded),
        capability_cards=_capability_cards(devices, services, active_turn, memory, jobs, recent_events),
        next_best_action=next_best_action,
    )


def _storyline(
    devices: list[RuntimeDevice],
    active_turn: RuntimeTurn | None,
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
            title="小忆处理",
            detail=(
                f"最近一次交互状态：{_friendly_status(active_turn.status)}"
                if active_turn
                else (
                    f"最近一次交互事件：{turn_event.summary}"
                    if turn_event
                    else "等待一次真实对话或 PTT 输入"
                )
            ),
            status=_stage_status(active_turn.status) if active_turn else ("done" if turn_event else "pending"),
            source="agent",
            ts=active_turn.started_at if active_turn else (turn_event.ts if turn_event else None),
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
            ts=camera_event.ts if camera_event else None,
        ),
    ]


def _experience_lanes(
    devices: list[RuntimeDevice],
    services: list[RuntimeService],
    active_turn: RuntimeTurn | None,
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
            status="done" if online_devices else "pending",
            items=[
                RuntimeLaneItem(
                    label=device.role,
                    value=device.name or device.device_id,
                    status="done" if device.online else "idle",
                    detail="、".join(_friendly_capability(cap) for cap in device.capabilities[:4]),
                )
                for device in devices[:5]
            ],
        ),
        RuntimeLane(
            key="turn",
            title="一次交互",
            headline=(
                f"最近一次交互：{_friendly_status(active_turn.status)}"
                if active_turn
                else "等待用户开口"
            ),
            detail="这里展示从身体输入到小忆回应的关键步骤，不展示私密原文。",
            status=_stage_status(active_turn.status) if active_turn else "pending",
            items=[
                RuntimeLaneItem(
                    label=str(stage.get("label") or ""),
                    value=_friendly_status(stage.get("status")),
                    status=str(stage.get("status") or "pending"),
                    detail=(f"{stage.get('latency_ms')} ms" if stage.get("latency_ms") is not None else ""),
                )
                for stage in (active_turn.stages if active_turn else [])
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
    active_turn: RuntimeTurn | None,
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
            metric="已接入" if active_turn else "待触发",
            status="done" if active_turn else "pending",
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


def _default_companion(companions: list[Any]) -> Any | None:
    for row in companions:
        if getattr(row, "status", "") == "active":
            return row
    return companions[0] if companions else None


def _event_summary(event_type: str, subject_type: str, subject_id: str) -> str:
    readable = event_type.replace(".", " ")
    return f"{readable} · {subject_type}:{subject_id}"


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
        "agent": "小忆大脑",
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
        "agent": "小忆大脑",
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
    active_turn: RuntimeTurn | None,
    memory: RuntimeMemory,
    jobs: list[RuntimeJob],
    events: list[RuntimeEvent],
) -> int:
    checks = [
        bool(devices),
        any(device.online for device in devices),
        bool(services) and any(service.online for service in services),
        active_turn is not None,
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
