"""Tests for /api/overview/services — composite supervisor + HTTP view."""
from __future__ import annotations

import asyncio
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

import httpx
import pytest
import respx

from eidolon_admin_server.app.main import create_app
from eidolon_admin_server.app.settings import (
    AdminBindConfig,
    AuthConfig,
    GatewayConfig,
    ServiceConfig,
    Settings,
    SupervisorRef,
)


pytestmark = pytest.mark.asyncio


def _settings(tmp_path: Path, socket_path: Path, available: Path, enabled: Path) -> Settings:
    (tmp_path / "svc.yaml").write_text("services: []\n")
    return Settings(
        services_file=tmp_path / "svc.yaml",
        supervisor_socket=socket_path,
        supervisor_available_dir=available,
        supervisor_enabled_dir=enabled,
    )


def _services_without_supervisor():
    """A fully HTTP-driven service (no supervisor block)."""
    return [
        ServiceConfig(
            id="hub",
            name="Hub",
            base_url="http://hub.test:8082",
            upstream_prefix="/api/admin",
            auth=AuthConfig(type="none"),
            health="/api/admin/probe/health",
            features=[],
        ),
    ]


async def _http(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw")


@respx.mock
async def test_overview_http_only_service_online(tmp_path):
    respx.get("http://hub.test:8082/api/admin/probe/health").mock(
        return_value=httpx.Response(200, json={})
    )
    cfg = GatewayConfig(
        admin=AdminBindConfig(cors_origins=[]),
        services=_services_without_supervisor(),
    )
    settings = _settings(tmp_path, tmp_path / "missing.sock", tmp_path, tmp_path)
    app = create_app(cfg, settings=settings)
    async with await _http(app) as ac:
        resp = await ac.get("/api/overview/services")
    data = resp.json()
    assert data["supervisord_reachable"] is False
    hub = data["services"][0]
    assert hub["id"] == "hub"
    assert hub["supervised"] is False
    assert hub["online"] is True
    assert hub["http_probe"]["ok"] is True
    assert hub["programs"] == []


@respx.mock
async def test_overview_http_only_service_offline(tmp_path):
    respx.get("http://hub.test:8082/api/admin/probe/health").mock(
        side_effect=httpx.ConnectError("refused")
    )
    cfg = GatewayConfig(
        admin=AdminBindConfig(cors_origins=[]),
        services=_services_without_supervisor(),
    )
    settings = _settings(tmp_path, tmp_path / "missing.sock", tmp_path, tmp_path)
    app = create_app(cfg, settings=settings)
    async with await _http(app) as ac:
        resp = await ac.get("/api/overview/services")
    hub = resp.json()["services"][0]
    assert hub["online"] is False
    assert hub["http_probe"]["ok"] is False
    assert "refused" in hub["http_probe"]["error"]


@respx.mock
async def test_overview_health_accepts_absolute_url(tmp_path):
    """health: http://other.host/path bypasses base_url entirely."""
    respx.get("http://probe.test:9999/alive").mock(
        return_value=httpx.Response(200)
    )
    svc = ServiceConfig(
        id="memory",
        name="Memory",
        base_url="http://127.0.0.1:8010",
        upstream_prefix="/api",
        health="http://probe.test:9999/alive",
        features=[],
    )
    cfg = GatewayConfig(admin=AdminBindConfig(cors_origins=[]), services=[svc])
    settings = _settings(tmp_path, tmp_path / "missing.sock", tmp_path, tmp_path)
    app = create_app(cfg, settings=settings)
    async with await _http(app) as ac:
        resp = await ac.get("/api/overview/services")
    mem = resp.json()["services"][0]
    assert mem["http_probe"]["url"] == "http://probe.test:9999/alive"
    assert mem["http_probe"]["ok"] is True


# --- Tests requiring a real supervisord -----------------------------------


SUPERVISORD = shutil.which("supervisord") or sys.exec_prefix + "/bin/supervisord"


def _supervisord_conf(tmp: Path, available: Path, enabled: Path, sock: Path) -> Path:
    conf = tmp / "supervisord.conf"
    conf.write_text(textwrap.dedent(f"""\
        [unix_http_server]
        file={sock}
        chmod=0700

        [supervisord]
        logfile={tmp}/supervisord.log
        pidfile={tmp}/supervisord.pid
        childlogdir={tmp}/childlogs
        nodaemon=false
        minfds=1024
        minprocs=200

        [rpcinterface:supervisor]
        supervisor.rpcinterface_factory = supervisor.rpcinterface:make_main_rpcinterface

        [supervisorctl]
        serverurl=unix://{sock}

        [include]
        files={enabled}/*.conf
    """))
    (tmp / "childlogs").mkdir(exist_ok=True)
    return conf


@pytest.fixture
async def supervisord_stack(tmp_path):
    available = tmp_path / "available"
    enabled = tmp_path / "enabled"
    available.mkdir()
    enabled.mkdir()

    # A long-running sleep program named "demo" in group "demo".
    (available / "demo.conf").write_text(textwrap.dedent("""\
        [program:demo]
        command=/bin/sh -c "sleep 600"
        autostart=true
        autorestart=false
        startsecs=0
        stdout_logfile=NONE
        stderr_logfile=NONE

        [group:demo]
        programs=demo
    """))
    # Enable it from the start.
    (enabled / "demo.conf").symlink_to(Path("..") / "available" / "demo.conf")

    # Also one infra-only program "natz" not claimed by any service.
    (available / "natz.conf").write_text(textwrap.dedent("""\
        [program:natz]
        command=/bin/sh -c "sleep 600"
        autostart=true
        autorestart=false
        startsecs=0
        stdout_logfile=NONE
        stderr_logfile=NONE

        [group:natz]
        programs=natz
    """))
    (enabled / "natz.conf").symlink_to(Path("..") / "available" / "natz.conf")

    sock_dir = Path(tempfile.mkdtemp(prefix="evsv-", dir="/tmp"))
    sock = sock_dir / "s.sock"
    conf = _supervisord_conf(tmp_path, available, enabled, sock)

    proc = subprocess.Popen([SUPERVISORD, "-c", str(conf), "-n"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    deadline = time.time() + 10
    while time.time() < deadline and not sock.exists():
        await asyncio.sleep(0.1)
    assert sock.exists(), "supervisord did not start"
    await asyncio.sleep(1.0)  # let programs reach RUNNING

    try:
        yield sock, available, enabled, tmp_path
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        shutil.rmtree(sock_dir, ignore_errors=True)


@respx.mock
async def test_overview_supervised_service_with_http_probe(supervisord_stack):
    sock, available, enabled, tmp_path = supervisord_stack
    respx.get("http://demo.test/alive").mock(return_value=httpx.Response(200))

    svc = ServiceConfig(
        id="demo",
        name="Demo",
        base_url="http://demo.test",
        upstream_prefix="",
        health="/alive",
        supervisor=SupervisorRef(group="demo", programs=["demo"]),
        features=[],
    )
    cfg = GatewayConfig(admin=AdminBindConfig(cors_origins=[]), services=[svc])
    settings = _settings(tmp_path, sock, available, enabled)
    app = create_app(cfg, settings=settings)
    async with await _http(app) as ac:
        resp = await ac.get("/api/overview/services")
    data = resp.json()

    assert data["supervisord_reachable"] is True
    demo = data["services"][0]
    assert demo["supervised"] is True
    assert demo["online"] is True
    assert len(demo["programs"]) == 1
    assert demo["programs"][0]["statename"] == "RUNNING"
    assert demo["http_probe"]["ok"] is True

    # natz, not claimed by any service, lands in infrastructure.
    infra_groups = {i["group"] for i in data["infrastructure"]}
    assert "natz" in infra_groups
    natz = next(i for i in data["infrastructure"] if i["group"] == "natz")
    assert natz["online"] is True


async def test_supervised_service_offline_when_http_fails(supervisord_stack):
    """Process RUNNING but port unreachable → online=False (catches stuck procs)."""
    sock, available, enabled, tmp_path = supervisord_stack

    svc = ServiceConfig(
        id="demo",
        name="Demo",
        base_url="http://127.0.0.1:65535",  # nothing listens
        upstream_prefix="",
        health="/alive",
        supervisor=SupervisorRef(group="demo", programs=["demo"]),
        features=[],
    )
    cfg = GatewayConfig(admin=AdminBindConfig(cors_origins=[]), services=[svc])
    settings = _settings(tmp_path, sock, available, enabled)
    app = create_app(cfg, settings=settings)
    async with await _http(app) as ac:
        resp = await ac.get("/api/overview/services")
    demo = resp.json()["services"][0]
    assert demo["programs"][0]["statename"] == "RUNNING"
    assert demo["http_probe"]["ok"] is False
    assert demo["online"] is False


async def test_supervised_service_online_without_http_when_no_probe(supervisord_stack):
    """If health is unset, fall back to 'all programs RUNNING'."""
    sock, available, enabled, tmp_path = supervisord_stack

    svc = ServiceConfig(
        id="demo",
        name="Demo",
        base_url="http://demo.test",
        upstream_prefix="",
        health=None,
        supervisor=SupervisorRef(group="demo", programs=["demo"]),
        features=[],
    )
    cfg = GatewayConfig(admin=AdminBindConfig(cors_origins=[]), services=[svc])
    settings = _settings(tmp_path, sock, available, enabled)
    app = create_app(cfg, settings=settings)
    async with await _http(app) as ac:
        resp = await ac.get("/api/overview/services")
    demo = resp.json()["services"][0]
    assert demo["online"] is True
    assert demo["http_probe"]["configured"] is False


