"""Tests for post-start service readiness.

The readiness contract is pure orchestration: supervisor process state plus
HTTP probe semantics. These tests use explicit test doubles for both, so they
exercise the boundary logic without depending on a live dev stack.
"""
from __future__ import annotations

import json
from collections.abc import Iterable

import pytest

from eidolon_admin_server.app.settings import (
    AdminBindConfig,
    AuthConfig,
    GatewayConfig,
    PortsDecl,
    ServiceConfig,
    SupervisorRef,
)
from eidolon_admin_server.app.supervisor.client import ProcessInfo
from eidolon_admin_server.app.system_health import cli
from eidolon_admin_server.app.system_health.readiness import (
    HttpProbeResult,
    HttpProbeSpec,
    assess_readiness,
    wait_for_readiness,
)


class _StubSupervisorClient:
    def __init__(self, processes: Iterable[ProcessInfo]) -> None:
        self._processes = list(processes)

    async def get_all_process_info(self) -> list[ProcessInfo]:
        return list(self._processes)


def _process(
    group: str,
    name: str,
    *,
    statename: str = "RUNNING",
    pid: int = 100,
) -> ProcessInfo:
    return ProcessInfo(
        name=name,
        group=group,
        state=20 if statename == "RUNNING" else 10,
        statename=statename,
        pid=pid if statename == "RUNNING" else 0,
        start=0,
        stop=0,
        now=0,
        exitstatus=0,
        description="",
        spawnerr="",
        logfile="",
        stderr_logfile="",
    )


def _service(
    service_id: str,
    *,
    programs: list[str],
    optional: bool = False,
    health: str | None = None,
    base_url: str = "",
) -> ServiceConfig:
    return ServiceConfig(
        id=service_id,
        name=service_id.title(),
        integration="native",
        optional=optional,
        base_url=base_url,
        health=health,
        auth=AuthConfig(type="none"),
        supervisor=SupervisorRef(
            config_file=f"{service_id}.conf",
            group=service_id,
            programs=programs,
        ),
        ports=PortsDecl(declared=[]),
    )


def _config(*services: ServiceConfig) -> GatewayConfig:
    return GatewayConfig(
        admin=AdminBindConfig(host="127.0.0.1", port=9000, cors_origins=[]),
        services=list(services),
    )


async def _ok_probe(spec: HttpProbeSpec) -> HttpProbeResult:
    return HttpProbeResult(
        name=spec.name,
        url=spec.url,
        ok=True,
        status_code=200,
        latency_ms=1.0,
    )


async def _failed_probe(spec: HttpProbeSpec) -> HttpProbeResult:
    return HttpProbeResult(
        name=spec.name,
        url=spec.url,
        ok=False,
        status_code=503,
        latency_ms=1.0,
        detail="not ready",
    )


@pytest.mark.asyncio
async def test_core_readiness_requires_programs_and_semantic_probes() -> None:
    cfg = _config(
        _service("admin", programs=["admin-api"]),
        _service("agent", programs=["agent"]),
        _service("memory", programs=["memory-supervisor", "memory-discovery"]),
        _service("nats", programs=["nats-server"]),
    )
    supervisor = _StubSupervisorClient([
        _process("admin", "admin-api", pid=101),
        _process("agent", "agent", pid=102),
        _process("memory", "memory-supervisor", pid=103),
        _process("memory", "memory-discovery", pid=104),
        _process("nats", "nats-server", pid=105),
    ])
    seen: list[str] = []

    async def probe(spec: HttpProbeSpec) -> HttpProbeResult:
        seen.append(spec.name)
        return await _ok_probe(spec)

    report = await assess_readiness(
        cfg,
        supervisor,  # type: ignore[arg-type]
        service_ids=("admin", "agent", "memory", "nats"),
        http_probe=probe,
    )

    assert report.ok is True
    assert {svc.service_id for svc in report.services} == {
        "admin",
        "agent",
        "memory",
        "nats",
    }
    assert {
        "admin_api_services",
        "agent_http_readyz",
        "agent_admin_openapi",
        "memory_discovery_routing",
        "nats_varz",
    } <= set(seen)


@pytest.mark.asyncio
async def test_missing_required_program_blocks_readiness() -> None:
    cfg = _config(_service("agent", programs=["agent"]))
    report = await assess_readiness(
        cfg,
        _StubSupervisorClient([]),  # type: ignore[arg-type]
        service_ids=("agent",),
        http_probe=_ok_probe,
    )

    assert report.ok is False
    assert report.services[0].blocking is True
    assert report.services[0].programs[0].statename == "UNKNOWN"


@pytest.mark.asyncio
async def test_optional_degraded_service_does_not_block_unless_strict() -> None:
    cfg = _config(
        _service(
            "mementos",
            programs=["mementos"],
            optional=True,
            health="http://127.0.0.1:18765/health",
        )
    )
    supervisor = _StubSupervisorClient([
        _process("mementos", "mementos", statename="STOPPED")
    ])

    relaxed = await assess_readiness(
        cfg,
        supervisor,  # type: ignore[arg-type]
        service_ids=("mementos",),
        http_probe=_failed_probe,
    )
    strict = await assess_readiness(
        cfg,
        supervisor,  # type: ignore[arg-type]
        service_ids=("mementos",),
        strict=True,
        http_probe=_failed_probe,
    )

    assert relaxed.ok is True
    assert relaxed.services[0].ok is False
    assert relaxed.services[0].blocking is False
    assert strict.ok is False
    assert strict.services[0].blocking is True


@pytest.mark.asyncio
async def test_unknown_enabled_group_gets_process_only_readiness() -> None:
    cfg = _config()
    report = await assess_readiness(
        cfg,
        _StubSupervisorClient([
            _process("livekit", "livekit-server", pid=201)
        ]),  # type: ignore[arg-type]
        service_ids=("livekit",),
        include_supervisor_groups=True,
        http_probe=_failed_probe,
    )

    assert report.ok is True
    service = report.services[0]
    assert service.service_id == "livekit"
    assert service.kind == "supervisor_group"
    assert service.http == []
    assert "process-only readiness" in service.notes[0]


@pytest.mark.asyncio
async def test_wait_retries_until_probe_becomes_ready() -> None:
    cfg = _config(_service("admin", programs=["admin-api"]))
    supervisor = _StubSupervisorClient([
        _process("admin", "admin-api", pid=301)
    ])
    calls = 0

    async def flaky_probe(spec: HttpProbeSpec) -> HttpProbeResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            return await _failed_probe(spec)
        return await _ok_probe(spec)

    report, attempts = await wait_for_readiness(
        cfg,
        supervisor,  # type: ignore[arg-type]
        service_ids=("admin",),
        timeout_seconds=1.0,
        interval_seconds=0.0,
        http_probe=flaky_probe,
    )

    assert report.ok is True
    assert attempts == 2


@pytest.mark.asyncio
async def test_cli_wait_json_uses_injected_readiness_dependencies(capsys) -> None:
    cfg = _config(_service("admin", programs=["admin-api"]))
    supervisor = _StubSupervisorClient([
        _process("admin", "admin-api", pid=401)
    ])

    exit_code = await cli.wait(
        timeout_seconds=0.1,
        interval_seconds=0.0,
        service_ids=("admin",),
        json_output=True,
        cfg=cfg,
        supervisor_client=supervisor,  # type: ignore[arg-type]
        http_probe=_ok_probe,
    )

    assert exit_code == 0
    body = json.loads(capsys.readouterr().out)
    assert body["ok"] is True
    assert body["attempts"] == 1
    assert body["services"][0]["service_id"] == "admin"
