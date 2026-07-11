"""Service readiness contract for local dev startup.

This layer answers a different question from ``auditor.py``:

* auditor: are declared ports owned by the expected process tree?
* readiness: can upstream callers actually use the selected services?

The checker is intentionally read-only. It never creates owners,
companions, memory realms, or any other product data; deeper product
contracts live in the live E2E harness.
"""
from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx

from ..settings import GatewayConfig, ServiceConfig
from ..supervisor.client import (
    ProcessInfo,
    SupervisorClient,
    SupervisorError,
    SupervisorUnavailable,
)


HttpProbeFunc = Callable[["HttpProbeSpec"], Awaitable["HttpProbeResult"]]


@dataclass(frozen=True)
class HttpProbeSpec:
    name: str
    url: str
    validator: str = "generic_2xx"
    timeout_seconds: float = 2.0


@dataclass(frozen=True)
class HttpProbeResult:
    name: str
    url: str
    ok: bool
    status_code: int | None = None
    latency_ms: float | None = None
    error: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class ProgramReadiness:
    full_name: str
    ok: bool
    statename: str
    pid: int
    description: str = ""
    spawnerr: str = ""


@dataclass(frozen=True)
class ServiceReadiness:
    service_id: str
    name: str
    optional: bool
    kind: str
    ok: bool
    blocking: bool
    programs: list[ProgramReadiness] = field(default_factory=list)
    http: list[HttpProbeResult] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class StackReadiness:
    ok: bool
    strict: bool
    supervisord_reachable: bool
    elapsed_ms: float
    services: list[ServiceReadiness]
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def readiness_to_dict(report: StackReadiness) -> dict[str, Any]:
    return report.to_dict()


def parse_service_ids(raw: str | None) -> tuple[str, ...] | None:
    if raw is None:
        return None
    values = tuple(part.strip() for part in raw.split(",") if part.strip())
    return values or None


async def assess_readiness(
    cfg: GatewayConfig,
    supervisor_client: SupervisorClient,
    *,
    service_ids: Iterable[str] | None = None,
    include_supervisor_groups: bool = False,
    include_admin_web: bool = False,
    strict: bool = False,
    http_probe: HttpProbeFunc | None = None,
) -> StackReadiness:
    """Single readiness snapshot for the selected services.

    ``include_supervisor_groups`` lets full-stack dev startup include
    enabled supervisord groups that are not represented in services.yaml
    yet (currently LiveKit). Those groups get process readiness only.
    """
    start = time.perf_counter()
    try:
        infos = await supervisor_client.get_all_process_info()
        supervisord_reachable = True
        supervisor_error = None
    except (SupervisorUnavailable, SupervisorError) as exc:
        infos = []
        supervisord_reachable = False
        supervisor_error = str(exc)

    by_full = {info.full_name: info for info in infos}
    by_group: dict[str, list[ProcessInfo]] = {}
    for info in infos:
        by_group.setdefault(info.group, []).append(info)

    known_services, unknown_groups = _select_services(
        cfg,
        service_ids,
        include_supervisor_groups=include_supervisor_groups,
    )
    probe = http_probe or _default_http_probe

    service_reports: list[ServiceReadiness] = []
    for svc in known_services:
        service_reports.append(
            await _assess_service(
                cfg,
                svc,
                by_full,
                probe,
                include_admin_web=include_admin_web,
                strict=strict,
            )
        )

    for group in unknown_groups:
        service_reports.append(
            _assess_infrastructure_group(group, by_group.get(group, []), strict=strict)
        )

    ok = supervisord_reachable and all(not service.blocking for service in service_reports)
    return StackReadiness(
        ok=ok,
        strict=strict,
        supervisord_reachable=supervisord_reachable,
        elapsed_ms=round((time.perf_counter() - start) * 1000, 1),
        services=service_reports,
        error=supervisor_error,
    )


async def wait_for_readiness(
    cfg: GatewayConfig,
    supervisor_client: SupervisorClient,
    *,
    service_ids: Iterable[str] | None = None,
    include_supervisor_groups: bool = False,
    include_admin_web: bool = False,
    strict: bool = False,
    timeout_seconds: float = 45.0,
    interval_seconds: float = 0.5,
    http_probe: HttpProbeFunc | None = None,
) -> tuple[StackReadiness, int]:
    """Poll until readiness passes or timeout expires.

    Returns ``(last_report, attempts)`` so the CLI can surface deterministic
    diagnostics without re-running any probes.
    """
    deadline = time.monotonic() + timeout_seconds
    attempts = 0
    last: StackReadiness | None = None

    while True:
        attempts += 1
        last = await assess_readiness(
            cfg,
            supervisor_client,
            service_ids=service_ids,
            include_supervisor_groups=include_supervisor_groups,
            include_admin_web=include_admin_web,
            strict=strict,
            http_probe=http_probe,
        )
        if last.ok:
            return last, attempts
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return last, attempts
        await asyncio.sleep(min(interval_seconds, remaining))


def _select_services(
    cfg: GatewayConfig,
    service_ids: Iterable[str] | None,
    *,
    include_supervisor_groups: bool,
) -> tuple[list[ServiceConfig], list[str]]:
    if service_ids is None:
        return list(cfg.services), []

    requested = [service_id.strip() for service_id in service_ids if service_id.strip()]
    by_id = {service.id: service for service in cfg.services}
    known: list[ServiceConfig] = []
    unknown: list[str] = []
    for service_id in requested:
        svc = by_id.get(service_id)
        if svc is None:
            unknown.append(service_id)
        else:
            known.append(svc)
    if unknown and not include_supervisor_groups:
        raise ValueError(f"unknown service id(s): {', '.join(sorted(unknown))}")
    return known, sorted(set(unknown))


async def _assess_service(
    cfg: GatewayConfig,
    svc: ServiceConfig,
    by_full: dict[str, ProcessInfo],
    http_probe: HttpProbeFunc,
    *,
    include_admin_web: bool,
    strict: bool,
) -> ServiceReadiness:
    programs = _program_readiness(svc, by_full)
    specs = _probe_specs_for_service(cfg, svc, include_admin_web=include_admin_web)
    http_results = await asyncio.gather(*(http_probe(spec) for spec in specs))

    has_contract = bool(programs or http_results)
    local_ok = has_contract
    if programs:
        local_ok = local_ok and all(program.ok for program in programs)
    if http_results:
        local_ok = local_ok and all(result.ok for result in http_results)

    notes: list[str] = []
    if not has_contract:
        notes.append("no supervisor program or HTTP readiness probe configured")

    blocking = (not local_ok) and (strict or not svc.optional)
    return ServiceReadiness(
        service_id=svc.id,
        name=svc.name,
        optional=svc.optional,
        kind="service",
        ok=local_ok,
        blocking=blocking,
        programs=programs,
        http=list(http_results),
        notes=notes,
    )


def _assess_infrastructure_group(
    group: str,
    infos: list[ProcessInfo],
    *,
    strict: bool,
) -> ServiceReadiness:
    programs = [
        _program_from_info(info)
        for info in sorted(infos, key=lambda item: item.full_name)
    ]
    if not programs:
        programs = [
            ProgramReadiness(
                full_name=f"{group}:*",
                ok=False,
                statename="UNKNOWN",
                pid=0,
                description="enabled supervisor group is not loaded",
            )
        ]
    ok = all(program.ok for program in programs)
    return ServiceReadiness(
        service_id=group,
        name=group,
        optional=False,
        kind="supervisor_group",
        ok=ok,
        blocking=not ok,
        programs=programs,
        http=[],
        notes=["process-only readiness; group is not declared in services.yaml"],
    )


def _program_readiness(
    svc: ServiceConfig,
    by_full: dict[str, ProcessInfo],
) -> list[ProgramReadiness]:
    if svc.supervisor is None:
        return []
    group = svc.supervisor.group or ""
    programs: list[ProgramReadiness] = []
    for program in svc.supervisor.programs:
        full_name = f"{group}:{program}" if group else program
        info = by_full.get(full_name)
        if info is None:
            programs.append(
                ProgramReadiness(
                    full_name=full_name,
                    ok=False,
                    statename="UNKNOWN",
                    pid=0,
                    description="program not loaded in supervisord",
                )
            )
        else:
            programs.append(_program_from_info(info))
    return programs


def _program_from_info(info: ProcessInfo) -> ProgramReadiness:
    return ProgramReadiness(
        full_name=info.full_name,
        ok=info.statename == "RUNNING",
        statename=info.statename,
        pid=info.pid,
        description=info.description,
        spawnerr=info.spawnerr,
    )


def _probe_specs_for_service(
    cfg: GatewayConfig,
    svc: ServiceConfig,
    *,
    include_admin_web: bool,
) -> list[HttpProbeSpec]:
    if svc.id == "admin":
        specs = [
            HttpProbeSpec(
                name="admin_api_services",
                url=f"http://127.0.0.1:{cfg.admin.port}/api/services",
                validator="admin_services",
            )
        ]
        if include_admin_web:
            web_port = os.environ.get("EIDOLON_ADMIN_WEB_PORT", "9001")
            specs.append(
                HttpProbeSpec(
                    name="admin_web",
                    url=f"http://127.0.0.1:{web_port}/",
                    validator="generic_2xx",
                )
            )
        return specs

    if svc.id == "agent":
        http_port = os.environ.get("EIDOLON_AGENT_HTTP_PORT", "8180")
        admin_port = os.environ.get("EIDOLON_AGENT_ADMIN_PORT", "8081")
        return [
            HttpProbeSpec(
                name="agent_http_readyz",
                url=f"http://127.0.0.1:{http_port}/readyz",
                validator="agent_readyz",
            ),
            HttpProbeSpec(
                name="agent_admin_openapi",
                url=f"http://127.0.0.1:{admin_port}/api/openapi.json",
                validator="agent_admin_openapi",
            ),
        ]

    if svc.id == "memory":
        port = os.environ.get("EIDOLON_MEMORY_DISCOVERY_PORT", "8020")
        return [
            HttpProbeSpec(
                name="memory_discovery_routing",
                url=(
                    f"http://127.0.0.1:{port}"
                    "/api/discovery/agent-routing"
                ),
                validator="memory_discovery",
            )
        ]

    if svc.id == "nats":
        port = os.environ.get("EIDOLON_NATS_HTTP_PORT", "8222")
        return [
            HttpProbeSpec(
                name="nats_varz",
                url=f"http://127.0.0.1:{port}/varz",
                validator="nats_varz",
            )
        ]

    url = _compose_probe_url(svc.base_url, svc.health)
    if url:
        return [HttpProbeSpec(name=f"{svc.id}_health", url=url)]
    return []


def _compose_probe_url(base_url: str, health: str | None) -> str | None:
    if not health:
        return None
    if health.startswith(("http://", "https://")):
        return health
    if not base_url:
        return None
    return f"{base_url}{health}"


async def _default_http_probe(spec: HttpProbeSpec) -> HttpProbeResult:
    async with httpx.AsyncClient(trust_env=False) as client:
        return await probe_http(client, spec)


async def probe_http(
    client: httpx.AsyncClient,
    spec: HttpProbeSpec,
) -> HttpProbeResult:
    start = time.perf_counter()
    try:
        response = await client.get(
            spec.url,
            timeout=spec.timeout_seconds,
            headers={"Connection": "close"},
        )
    except Exception as exc:  # noqa: BLE001
        return HttpProbeResult(
            name=spec.name,
            url=spec.url,
            ok=False,
            latency_ms=round((time.perf_counter() - start) * 1000, 1),
            error=type(exc).__name__,
            detail=str(exc),
        )

    latency_ms = round((time.perf_counter() - start) * 1000, 1)
    ok, detail = _validate_response(spec.validator, response)
    return HttpProbeResult(
        name=spec.name,
        url=spec.url,
        ok=ok,
        status_code=response.status_code,
        latency_ms=latency_ms,
        detail=detail,
    )


def _validate_response(validator: str, response: httpx.Response) -> tuple[bool, str | None]:
    if validator == "generic_2xx":
        return (200 <= response.status_code < 400), None

    if response.status_code != 200:
        return False, f"expected HTTP 200, got {response.status_code}"

    try:
        body = response.json()
    except ValueError:
        return False, "response is not JSON"

    if validator == "admin_services":
        services = body.get("services") if isinstance(body, dict) else None
        if isinstance(services, list):
            return True, None
        return False, "missing services list"

    if validator == "agent_readyz":
        if isinstance(body, dict) and body.get("status") == "ready":
            return True, None
        return False, "agent status is not ready"

    if validator == "agent_admin_openapi":
        info = body.get("info") if isinstance(body, dict) else None
        title = info.get("title") if isinstance(info, dict) else None
        if title == "eidolon-agent admin":
            return True, None
        return False, "unexpected agent admin OpenAPI title"

    if validator == "memory_discovery":
        if (
            isinstance(body, dict)
            and body.get("version")
            and isinstance(body.get("memory_realms"), list)
        ):
            return True, None
        return False, "invalid memory discovery contract"

    if validator == "nats_varz":
        if isinstance(body, dict) and (
            body.get("server_id") or body.get("server_name") or body.get("version")
        ):
            return True, None
        return False, "invalid NATS /varz contract"

    return False, f"unknown validator {validator}"
