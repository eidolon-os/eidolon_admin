"""Tests for the /api/memory/runners endpoint."""
from __future__ import annotations

import sqlite3
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


def _write_registry(tmp_path: Path, *, missing: bool = False) -> Path:
    db_path = tmp_path / "registry.sqlite3"
    if missing:
        return db_path
    conn = sqlite3.connect(db_path)
    with conn:
        conn.execute(
            """
            CREATE TABLE users (
                user_id TEXT PRIMARY KEY,
                enabled INTEGER NOT NULL,
                palace_path TEXT NOT NULL DEFAULT '',
                memory_port INTEGER NOT NULL DEFAULT 0,
                consolidator_enabled INTEGER NOT NULL DEFAULT 1,
                consolidator_interval_hours REAL NOT NULL DEFAULT 6.0,
                consolidator_window_days INTEGER NOT NULL DEFAULT 30,
                consolidator_min_drawers INTEGER NOT NULL DEFAULT 3,
                consolidator_min_confidence REAL NOT NULL DEFAULT 0.6
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO users (
                user_id, enabled, palace_path, memory_port,
                consolidator_enabled, consolidator_interval_hours,
                consolidator_window_days, consolidator_min_drawers,
                consolidator_min_confidence
            ) VALUES (?, ?, '', ?, 1, 6.0, 30, 3, 0.6)
            """,
            [
                ("alice", 1, 8030),
                ("bob", 1, 8031),
                ("disabled-carol", 0, 8032),
            ],
        )
    conn.close()
    return db_path


async def test_user_id_from_cmdline_requires_memory_space_id():
    f = runners_mod._user_id_from_cmdline
    assert f(["x", "--memory-space-id", "default.alice.default", "--port", "8030"]) == "alice"
    assert f(["x", "--memory-space-id=default.bob.default"]) == "bob"
    assert f(["x", "--memory-space-id=alice"]) is None
    assert f(["x", "--port", "8030"]) is None


async def _http(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw")


def _app(tmp_path, registry_db: Path):
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
    registry_db = _write_registry(tmp_path)
    monkeypatch.setenv("EIDOLON_REGISTRY_DB_PATH", str(registry_db))
    app = _app(tmp_path, registry_db)

    with (
        patch.object(runners_mod, "find_agent_processes", return_value={}),
        patch.object(runners_mod, "find_consolidator_processes", return_value={}),
    ):
        async with await _http(app) as ac:
            resp = await ac.get("/api/memory/runners")

    assert resp.status_code == 200
    data = resp.json()
    assert data["users_source_exists"] is True
    assert data["users_source"].endswith("registry.sqlite3")
    ids = [r["user_id"] for r in data["runners"]]
    assert ids == ["alice", "bob", "disabled-carol"]
    # None of them are running.
    assert all(r["pid"] is None and r["running"] is False for r in data["runners"])
    assert all(r["consolidator"]["running"] is False for r in data["runners"])
    assert data["consolidator_orphans"] == []
    # Disabled user is not probed.
    carol = next(r for r in data["runners"] if r["user_id"] == "disabled-carol")
    assert carol["listening"] is False
    assert data["orphans"] == []


async def test_endpoint_surfaces_orphan_processes(tmp_path, monkeypatch):
    registry_db = _write_registry(tmp_path)
    monkeypatch.setenv("EIDOLON_REGISTRY_DB_PATH", str(registry_db))
    app = _app(tmp_path, registry_db)

    class FakeProc:
        def __init__(self, pid: int, create_time: float):
            self.pid = pid
            self._create = create_time

        def oneshot(self):
            class _C:
                def __enter__(self_):
                    return None

                def __exit__(self_, *a):
                    return False

            return _C()

        def create_time(self):
            return self._create

        def memory_info(self):
            class _M:
                rss = 50 * 1024 * 1024

            return _M()

        def cpu_percent(self, interval=None):
            return 1.5

    fake_proc = FakeProc(pid=99001, create_time=0.0)
    # Map includes one orphan user not in the registry.
    fake_map = {"alice": fake_proc, "ghost": FakeProc(pid=99002, create_time=0.0)}

    with (
        patch.object(runners_mod, "find_agent_processes", return_value=fake_map),
        patch.object(runners_mod, "find_consolidator_processes", return_value={}),
    ):
        async with await _http(app) as ac:
            resp = await ac.get("/api/memory/runners")

    data = resp.json()
    alice = next(r for r in data["runners"] if r["user_id"] == "alice")
    assert alice["pid"] == 99001
    assert alice["running"] is True
    assert [o["user_id"] for o in data["orphans"]] == ["ghost"]


async def test_endpoint_when_registry_missing(tmp_path, monkeypatch):
    registry_db = _write_registry(tmp_path, missing=True)
    monkeypatch.setenv("EIDOLON_REGISTRY_DB_PATH", str(registry_db))
    app = _app(tmp_path, registry_db)

    with (
        patch.object(runners_mod, "find_agent_processes", return_value={}),
        patch.object(runners_mod, "find_consolidator_processes", return_value={}),
    ):
        async with await _http(app) as ac:
            resp = await ac.get("/api/memory/runners")

    data = resp.json()
    assert resp.status_code == 200
    assert data["users_source_exists"] is False
    assert data["runners"] == []
