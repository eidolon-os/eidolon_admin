"""Unified status overview — supervisor state + HTTP probe per service.

This is the single source of truth the frontend uses to render "is X online?".
For each service in services.yaml we report:

- ``supervised``: whether the service declares a ``supervisor:`` block
- ``programs``: live process state pulled from supervisord (RUNNING / FATAL / …)
- ``http_probe``: optional HTTP reachability check
- ``online``: composite verdict (see :func:`_compute_online`)

Programs that belong to a supervisord group not claimed by any service
(currently just ``nats``) are returned separately under ``infrastructure``.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
from fastapi import APIRouter, Request

from ..supervisor.client import (
    ProcessInfo,
    SupervisorClient,
    SupervisorError,
    SupervisorUnavailable,
)

router = APIRouter(prefix="/overview", tags=["overview"])


def _is_absolute(url_or_path: str) -> bool:
    return url_or_path.startswith(("http://", "https://"))


def _compose_probe_url(base_url: str, health: str | None) -> str | None:
    if not health:
        return None
    if _is_absolute(health):
        return health
    if not base_url:
        return None
    return f"{base_url}{health}"


async def _probe_http(
    client: httpx.AsyncClient, url: str | None
) -> dict[str, Any]:
    if not url:
        return {"configured": False}
    start = time.perf_counter()
    try:
        resp = await client.get(url, timeout=2.0)
        elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
        return {
            "configured": True,
            "url": url,
            "ok": resp.status_code < 500,
            "status_code": resp.status_code,
            "latency_ms": elapsed_ms,
        }
    except Exception as exc:  # noqa: BLE001 — surface any transport failure
        elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
        return {
            "configured": True,
            "url": url,
            "ok": False,
            "error": str(exc),
            "latency_ms": elapsed_ms,
        }


def _program_payload(info: ProcessInfo) -> dict[str, Any]:
    return {
        "name": info.name,
        "group": info.group,
        "full_name": info.full_name,
        "statename": info.statename,
        "pid": info.pid,
        "uptime_sec": max(0, info.now - info.start) if info.start else 0,
        "description": info.description,
        "spawnerr": info.spawnerr,
    }


def _compute_online(
    supervised: bool,
    programs: list[dict[str, Any]],
    http_probe: dict[str, Any],
) -> bool:
    """Composite online rule:

    - supervised + http configured  → all programs RUNNING AND http ok
    - supervised only               → all programs RUNNING
    - http only                     → http ok
    - neither                       → unknown (False)
    """
    http_configured = http_probe.get("configured", False)
    http_ok = http_probe.get("ok", False)
    if supervised:
        all_running = bool(programs) and all(
            p["statename"] == "RUNNING" for p in programs
        )
        if http_configured:
            return all_running and http_ok
        return all_running
    if http_configured:
        return http_ok
    return False


async def _get_all_processes(
    sv_client: SupervisorClient,
) -> tuple[bool, dict[str, ProcessInfo]]:
    """Returns (supervisord_reachable, {full_name: ProcessInfo})."""
    try:
        infos = await sv_client.get_all_process_info()
    except (SupervisorUnavailable, SupervisorError):
        return False, {}
    return True, {info.full_name: info for info in infos}


@router.get("/services")
async def overview(request: Request) -> dict[str, Any]:
    registry = request.app.state.registry
    http_client: httpx.AsyncClient = request.app.state.http_client
    sv_client: SupervisorClient = request.app.state.supervisor_client

    sv_ok, sv_by_full_name = await _get_all_processes(sv_client)
    consumed_groups: set[str] = set()

    # Parallelise HTTP probes across all services.
    probe_urls = [
        _compose_probe_url(svc.base_url, svc.health) for svc in registry.services
    ]
    probe_results = await asyncio.gather(
        *(_probe_http(http_client, url) for url in probe_urls)
    )

    services_payload: list[dict[str, Any]] = []
    for svc, http_probe in zip(registry.services, probe_results):
        supervised = svc.supervisor is not None
        programs: list[dict[str, Any]] = []
        if supervised and svc.supervisor:
            group = svc.supervisor.group
            if group:
                consumed_groups.add(group)
            for prog_name in svc.supervisor.programs:
                full = f"{group}:{prog_name}" if group else prog_name
                info = sv_by_full_name.get(full)
                if info is None:
                    # Program declared in services.yaml but supervisord doesn't
                    # know about it (e.g. config not enabled yet).
                    programs.append({
                        "name": prog_name,
                        "group": group or "",
                        "full_name": full,
                        "statename": "UNKNOWN",
                        "pid": 0,
                        "uptime_sec": 0,
                        "description": "not loaded in supervisord",
                        "spawnerr": "",
                    })
                else:
                    programs.append(_program_payload(info))

        online = _compute_online(supervised, programs, http_probe)
        services_payload.append({
            "id": svc.id,
            "name": svc.name,
            "supervised": supervised,
            "online": online,
            "programs": programs,
            "http_probe": http_probe,
        })

    # Anything in supervisord that no service claims becomes "infrastructure".
    infra_groups: dict[str, list[dict[str, Any]]] = {}
    for full_name, info in sv_by_full_name.items():
        if info.group in consumed_groups:
            continue
        infra_groups.setdefault(info.group, []).append(_program_payload(info))

    infrastructure_payload = [
        {
            "group": group,
            "programs": progs,
            "online": all(p["statename"] == "RUNNING" for p in progs),
        }
        for group, progs in sorted(infra_groups.items())
    ]

    return {
        "supervisord_reachable": sv_ok,
        "services": services_payload,
        "infrastructure": infrastructure_payload,
    }
