"""Tests for the /api/memory/runners endpoint."""
from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from eidolon_admin_server.app.main import create_app
from eidolon_admin_server.app.memory import runners as runners_mod
from eidolon_admin_server.app.settings import (
    AdminBindConfig,
    GatewayConfig,
    Settings,
)


pytestmark = pytest.mark.asyncio


def _write_users(tmp_path: Path) -> Path:
    p = tmp_path / "users.yaml"
    p.write_text(textwrap.dedent("""\
        users:
          - id: alice
            port: 8030
            enabled: true
            palace_path: ''
          - id: bob
            port: 8031
            enabled: true
            palace_path: ''
          - id: disabled-carol
            port: 8032
            enabled: false
            palace_path: ''
    """))
    return p


def test_load_users(tmp_path):
    p = _write_users(tmp_path)
    users = runners_mod.load_users(p)
    assert [u.id for u in users] == ["alice", "bob", "disabled-carol"]
    assert users[0].port == 8030
    assert users[2].enabled is False


def test_user_id_from_cmdline_supports_both_forms():
    f = runners_mod._user_id_from_cmdline
    assert f(["x", "--user-id", "alice", "--port", "8030"]) == "alice"
    assert f(["x", "--user-id=bob"]) == "bob"
    assert f(["x", "--port", "8030"]) is None


async def _http(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw")


def _app(tmp_path, users_yaml: Path):
    settings = Settings(
        services_file=tmp_path / "svc.yaml",
        supervisor_socket=tmp_path / "missing.sock",
        supervisor_available_dir=tmp_path / "available",
        supervisor_enabled_dir=tmp_path / "enabled",
    )
    (tmp_path / "available").mkdir(exist_ok=True)
    (tmp_path / "enabled").mkdir(exist_ok=True)
    (tmp_path / "svc.yaml").write_text("services: []\n")
    return create_app(
        GatewayConfig(admin=AdminBindConfig(cors_origins=[]), services=[]),
        settings=settings,
    )


async def test_endpoint_lists_users_with_no_processes(tmp_path, monkeypatch):
    p = _write_users(tmp_path)
    monkeypatch.setenv("EIDOLON_MEMORY_USERS_YAML", str(p))
    app = _app(tmp_path, p)

    with patch.object(runners_mod, "find_agent_processes", return_value={}):
        async with await _http(app) as ac:
            resp = await ac.get("/api/memory/runners")

    assert resp.status_code == 200
    data = resp.json()
    assert data["users_yaml_exists"] is True
    ids = [r["user_id"] for r in data["runners"]]
    assert ids == ["alice", "bob", "disabled-carol"]
    # None of them are running.
    assert all(r["pid"] is None and r["running"] is False for r in data["runners"])
    # Disabled user is not probed.
    carol = next(r for r in data["runners"] if r["user_id"] == "disabled-carol")
    assert carol["listening"] is False
    assert data["orphans"] == []


async def test_endpoint_surfaces_orphan_processes(tmp_path, monkeypatch):
    p = _write_users(tmp_path)
    monkeypatch.setenv("EIDOLON_MEMORY_USERS_YAML", str(p))
    app = _app(tmp_path, p)

    class FakeProc:
        def __init__(self, pid: int, create_time: float):
            self.pid = pid
            self._create = create_time

        def oneshot(self):
            class _C:
                def __enter__(self_): return None
                def __exit__(self_, *a): return False
            return _C()

        def create_time(self):
            return self._create

        def memory_info(self):
            class _M: rss = 50 * 1024 * 1024
            return _M()

        def cpu_percent(self, interval=None):
            return 1.5

    fake_proc = FakeProc(pid=99001, create_time=0.0)
    # Map includes one orphan user not in users.yaml.
    fake_map = {"alice": fake_proc, "ghost": FakeProc(pid=99002, create_time=0.0)}

    with patch.object(runners_mod, "find_agent_processes", return_value=fake_map):
        async with await _http(app) as ac:
            resp = await ac.get("/api/memory/runners")

    data = resp.json()
    alice = next(r for r in data["runners"] if r["user_id"] == "alice")
    assert alice["pid"] == 99001
    assert alice["running"] is True
    assert [o["user_id"] for o in data["orphans"]] == ["ghost"]


async def test_endpoint_when_users_yaml_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("EIDOLON_MEMORY_USERS_YAML", str(tmp_path / "missing.yaml"))
    app = _app(tmp_path, tmp_path / "missing.yaml")

    with patch.object(runners_mod, "find_agent_processes", return_value={}):
        async with await _http(app) as ac:
            resp = await ac.get("/api/memory/runners")

    data = resp.json()
    assert resp.status_code == 200
    assert data["users_yaml_exists"] is False
    assert data["runners"] == []
