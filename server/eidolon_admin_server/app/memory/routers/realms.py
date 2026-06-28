"""Read endpoints for memory realms."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter

from ..mcp_client import call_tool, mcp_url_for_port, probe_reachable
from ..presentation import agent_log_path_for, consolidator_status, memory_runtime_state
from ..runners import (
    RealmEntry,
    find_agent_processes,
    find_consolidator_processes,
    load_realms,
    realms_source_path,
)
from ..schemas import RealmDetail, RealmsListResponse

router = APIRouter()


def _default_memory_realm_id(entries: list[RealmEntry]) -> str:
    if not entries:
        return ""
    enabled_entry = next((e for e in entries if e.enabled), None)
    return (enabled_entry or entries[0]).memory_realm_id


async def _build_detail(
    entry: RealmEntry,
    *,
    agent_map: dict,
    cons_map: dict,
) -> RealmDetail:
    proc = agent_map.get(entry.id)
    detail = RealmDetail(
        memory_realm_id=entry.memory_realm_id,
        owner_id=entry.owner_id,
        companion_id=entry.companion_id,
        port=entry.port,
        enabled=entry.enabled,
        engine=entry.engine,
        status=entry.status,
        palace_path=entry.palace_path,
        mcp_http_url=mcp_url_for_port(entry.port),
        agent_log_path=agent_log_path_for(entry),
        log_path=agent_log_path_for(entry),
        pid=proc.pid if proc else None,
        worker_running=proc is not None,
        consolidator=consolidator_status(entry, cons_map=cons_map),
    )
    if not entry.enabled:
        detail.runtime_state = memory_runtime_state(
            entry,
            worker_running=detail.worker_running,
            agent_reachable=detail.agent_reachable,
            palace_initialized=detail.palace_initialized,
        )
        return detail
    detail.agent_reachable = await probe_reachable(entry.memory_realm_id)
    if detail.agent_reachable:
        try:
            status = await call_tool(entry.memory_realm_id, "eidolon_memory_status")
            if isinstance(status, dict):
                detail.runner_status = status
                detail.palace_initialized = bool(
                    status.get("palace_initialized") or status.get("ready")
                )
        except Exception:  # noqa: BLE001 - best-effort enrichment
            pass
    detail.runtime_state = memory_runtime_state(
        entry,
        worker_running=detail.worker_running,
        agent_reachable=detail.agent_reachable,
        palace_initialized=detail.palace_initialized,
    )
    return detail


@router.get("/realms", response_model=RealmsListResponse)
async def list_memory_realms() -> RealmsListResponse:
    entries = load_realms()
    agent_map = find_agent_processes()
    cons_map = find_consolidator_processes()
    details = await asyncio.gather(
        *(_build_detail(e, agent_map=agent_map, cons_map=cons_map) for e in entries)
    )
    return RealmsListResponse(
        realms_source=str(realms_source_path()),
        realms=details,
        default_memory_realm_id=_default_memory_realm_id(entries),
    )
