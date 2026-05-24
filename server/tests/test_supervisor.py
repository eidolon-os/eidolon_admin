"""End-to-end tests for the supervisor module against a live supervisord.

Each test launches a temporary supervisord pointing at a per-test socket and
config tree under tmp_path, exercises the admin REST API via ASGITransport,
then tears the daemon down.
"""
from __future__ import annotations

import asyncio
import os
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

from eidolon_admin_server.app.main import create_app
from eidolon_admin_server.app.settings import (
    AdminBindConfig,
    GatewayConfig,
    Settings,
)
from eidolon_admin_server.app.supervisor.client import SupervisorClient


pytestmark = pytest.mark.asyncio


SUPERVISORD = shutil.which("supervisord") or sys.exec_prefix + "/bin/supervisord"
PYTHON = sys.executable


def _supervisord_conf(tmp: Path, available: Path, enabled: Path, socket: Path) -> Path:
    """Write a minimal supervisord master config in tmp."""
    conf = tmp / "supervisord.conf"
    conf.write_text(textwrap.dedent(f"""\
        [unix_http_server]
        file={socket}
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
        serverurl=unix://{socket}

        [include]
        files={enabled}/*.conf
    """))
    (tmp / "childlogs").mkdir(exist_ok=True)
    return conf


def _sleep_program_conf(name: str, duration: int = 60) -> str:
    """Generate a program block that just runs `sleep` so we can manipulate it."""
    return textwrap.dedent(f"""\
        [program:{name}]
        command=/bin/sh -c "echo started-{name}; sleep {duration}"
        autostart=true
        autorestart=false
        startsecs=0
        stdout_logfile=NONE
        stderr_logfile=NONE

        [group:{name}]
        programs={name}
    """)


@pytest.fixture
async def stack(tmp_path):
    """Spin up a real supervisord + ASGI app pointing at it.

    Unix sockets on macOS are capped at ~104 bytes of path. pytest's tmp_path
    in /private/var/folders/... already burns ~80 of those, so we put the
    *socket* under a short /tmp directory and keep configs/logs under tmp_path.
    """
    available = tmp_path / "available"
    enabled = tmp_path / "enabled"
    available.mkdir()
    enabled.mkdir()

    # Seed: one program in available, NOT enabled.
    (available / "alpha.conf").write_text(_sleep_program_conf("alpha"))

    sock_dir = Path(tempfile.mkdtemp(prefix="evsv-", dir="/tmp"))
    socket_path = sock_dir / "s.sock"
    conf = _supervisord_conf(tmp_path, available, enabled, socket_path)

    proc = subprocess.Popen(
        [SUPERVISORD, "-c", str(conf), "-n"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    # Wait for socket to appear.
    deadline = time.time() + 10
    while time.time() < deadline and not socket_path.exists():
        await asyncio.sleep(0.1)
    assert socket_path.exists(), "supervisord did not start in time"
    # Give the rpc interface a moment more.
    await asyncio.sleep(0.3)

    settings = Settings(
        services_file=tmp_path / "services-empty.yaml",
        supervisor_socket=socket_path,
        supervisor_available_dir=available,
        supervisor_enabled_dir=enabled,
    )
    (tmp_path / "services-empty.yaml").write_text("services: []\n")

    gw = GatewayConfig(
        admin=AdminBindConfig(host="127.0.0.1", port=9000, cors_origins=[]),
        services=[],
    )
    app = create_app(gw, settings=settings)

    try:
        yield app, settings, tmp_path
    finally:
        try:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        finally:
            for child_pid_file in tmp_path.glob("*.pid"):
                try:
                    pid = int(child_pid_file.read_text().strip())
                    os.kill(pid, signal.SIGKILL)
                except (ValueError, ProcessLookupError, FileNotFoundError):
                    pass
            shutil.rmtree(sock_dir, ignore_errors=True)


async def _http(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw")


async def test_state_reports_socket(stack):
    app, settings, _ = stack
    async with await _http(app) as ac:
        resp = await ac.get("/api/supervisor/state")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ping"] is True
    assert data["socket"] == str(settings.supervisor_socket)


async def test_list_configs_starts_with_disabled_alpha(stack):
    app, _, _ = stack
    async with await _http(app) as ac:
        resp = await ac.get("/api/supervisor/configs")
    data = resp.json()
    names = {c["name"]: c for c in data["configs"]}
    assert "alpha" in names
    assert names["alpha"]["enabled"] is False
    assert "alpha" in names["alpha"]["programs"]


async def test_enable_then_program_runs(stack):
    app, _, _ = stack
    async with await _http(app) as ac:
        resp = await ac.post("/api/supervisor/configs/alpha/enable")
        assert resp.status_code == 200
        assert resp.json()["enabled"] is True

        # Give supervisor a beat to launch.
        await asyncio.sleep(1.0)

        resp = await ac.get("/api/supervisor/programs")
        names = [p["full_name"] for p in resp.json()["programs"]]
        assert "alpha:alpha" in names

        resp = await ac.get("/api/supervisor/programs/alpha:alpha")
        info = resp.json()
        assert info["statename"] in {"RUNNING", "STARTING"}


async def test_stop_and_restart_program(stack):
    app, _, _ = stack
    async with await _http(app) as ac:
        await ac.post("/api/supervisor/configs/alpha/enable")
        await asyncio.sleep(1.0)

        resp = await ac.post("/api/supervisor/programs/alpha:alpha/stop")
        assert resp.status_code == 200

        info = (await ac.get("/api/supervisor/programs/alpha:alpha")).json()
        assert info["statename"] in {"STOPPED", "EXITED"}

        resp = await ac.post("/api/supervisor/programs/alpha:alpha/start")
        assert resp.status_code == 200
        await asyncio.sleep(0.5)
        info = (await ac.get("/api/supervisor/programs/alpha:alpha")).json()
        assert info["statename"] == "RUNNING"


async def test_disable_removes_symlink_and_stops_process(stack):
    app, settings, _ = stack
    async with await _http(app) as ac:
        await ac.post("/api/supervisor/configs/alpha/enable")
        await asyncio.sleep(1.0)

        link = settings.supervisor_enabled_dir / "alpha.conf"
        assert link.is_symlink()

        resp = await ac.post("/api/supervisor/configs/alpha/disable")
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False
        assert not link.exists() and not link.is_symlink()

        names = [p["full_name"] for p in (await ac.get("/api/supervisor/programs")).json()["programs"]]
        assert "alpha:alpha" not in names


async def test_put_config_persists_and_reread_picks_up_new_program(stack):
    app, settings, tmp_path = stack
    async with await _http(app) as ac:
        # Write a second program via PUT.
        new_text = _sleep_program_conf("alpha") + "\n" + _sleep_program_conf("beta")
        resp = await ac.put("/api/supervisor/configs/alpha", json={"text": new_text})
        assert resp.status_code == 200
        assert set(resp.json()["programs"]) == {"alpha", "beta"}

        # File on disk has both.
        assert "program:beta" in (settings.supervisor_available_dir / "alpha.conf").read_text()

        # Enable + supervisord should now know about both groups.
        resp = await ac.post("/api/supervisor/configs/alpha/enable")
        await asyncio.sleep(1.0)
        infos = (await ac.get("/api/supervisor/programs")).json()["programs"]
        full = {p["full_name"] for p in infos}
        assert {"alpha:alpha", "beta:beta"}.issubset(full)


async def test_put_rejects_invalid_ini(stack):
    app, _, _ = stack
    async with await _http(app) as ac:
        resp = await ac.put(
            "/api/supervisor/configs/alpha",
            json={"text": "this is not [valid\nini\n"},
        )
        assert resp.status_code == 400


async def test_invalid_name_rejected(stack):
    app, _, _ = stack
    async with await _http(app) as ac:
        resp = await ac.get("/api/supervisor/configs/../etc/passwd")
        # FastAPI normalises the path; either 404 or 400 is acceptable.
        assert resp.status_code in {400, 404}


async def test_state_when_socket_missing(tmp_path):
    """If supervisord isn't running, /state should report ping: False, not 500."""
    settings = Settings(
        services_file=tmp_path / "svc.yaml",
        supervisor_socket=tmp_path / "missing.sock",
        supervisor_available_dir=tmp_path,
        supervisor_enabled_dir=tmp_path,
    )
    (tmp_path / "svc.yaml").write_text("services: []\n")
    app = create_app(
        GatewayConfig(admin=AdminBindConfig(cors_origins=[]), services=[]),
        settings=settings,
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw") as ac:
        resp = await ac.get("/api/supervisor/state")
    assert resp.status_code == 200
    assert resp.json()["ping"] is False
