"""Tests for the /api/memory/runners endpoint."""
from __future__ import annotations

import json
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


def _write_eidolon_data(tmp_path: Path, *, missing: bool = False) -> Path:
    db_path = tmp_path / "eidolon.sqlite3"
    if missing:
        return db_path
    conn = sqlite3.connect(db_path)
    with conn:
        conn.execute(
            """
            CREATE TABLE owners (
                owner_id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL DEFAULT '',
                kind TEXT NOT NULL DEFAULT 'person',
                profile_json TEXT NOT NULL DEFAULT '{}',
                settings_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE memory_realms (
                realm_id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                companion_id TEXT NOT NULL,
                engine TEXT NOT NULL DEFAULT 'mempalace',
                engine_config_json TEXT NOT NULL DEFAULT '{}',
                policy_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO owners (
                owner_id, display_name, kind, profile_json, settings_json
            ) VALUES (?, ?, 'person', ?, ?)
            """,
            [
                (
                    "alice",
                    "Alice",
                    json.dumps({"registry": {"enabled": True}}),
                    json.dumps({"memory_port": 8030, "consolidator": {"enabled": True}}),
                ),
                (
                    "bob",
                    "Bob",
                    json.dumps({"registry": {"enabled": True}}),
                    json.dumps({"memory_port": 8031, "consolidator": {"enabled": True}}),
                ),
                (
                    "disabled-carol",
                    "Carol",
                    json.dumps({"registry": {"enabled": False}}),
                    json.dumps({"memory_port": 8032, "consolidator": {"enabled": True}}),
                ),
            ],
        )
        conn.executemany(
            """
            INSERT INTO memory_realms (
                realm_id, owner_id, companion_id, engine, engine_config_json, policy_json, status
            ) VALUES (?, ?, ?, 'mempalace', '{}', '{}', ?)
            """,
            [
                ("r:alice:default", "alice", "default", "active"),
                ("r:bob:default", "bob", "default", "active"),
                ("r:carol:default", "disabled-carol", "default", "active"),
            ],
        )
    conn.close()
    return db_path


async def test_memory_realm_id_from_cmdline_preserves_opaque_space_id():
    f = runners_mod._memory_realm_id_from_cmdline
    assert f(["x", "--memory-space-id", "r:alice:default", "--port", "8030"]) == "r:alice:default"
    assert f(["x", "--memory-space-id=default.bob.default"]) == "default.bob.default"
    assert f(["x", "--port", "8030"]) is None


async def _http(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw")


def _app(tmp_path):
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
    data_db = _write_eidolon_data(tmp_path)
    monkeypatch.setenv("EIDOLON_DATA_SQLITE_PATH", str(data_db))
    app = _app(tmp_path)

    with (
        patch.object(runners_mod, "find_agent_processes", return_value={}),
        patch.object(runners_mod, "find_consolidator_processes", return_value={}),
    ):
        async with await _http(app) as ac:
            resp = await ac.get("/api/memory/runners")

    assert resp.status_code == 200
    data = resp.json()
    assert data["realms_source_exists"] is True
    assert data["realms_source"].endswith("eidolon.sqlite3")
    assert data["realms_source_type"] == "eidolon_data"
    ids = [r["memory_realm_id"] for r in data["runners"]]
    assert ids == ["r:alice:default", "r:bob:default", "r:carol:default"]
    # None of them are running.
    assert all(r["pid"] is None and r["running"] is False for r in data["runners"])
    assert all(r["consolidator"]["running"] is False for r in data["runners"])
    assert data["consolidator_orphans"] == []
    # Disabled user is not probed.
    carol = next(r for r in data["runners"] if r["memory_realm_id"] == "r:carol:default")
    assert carol["listening"] is False
    assert data["orphans"] == []


async def test_endpoint_surfaces_orphan_processes(tmp_path, monkeypatch):
    data_db = _write_eidolon_data(tmp_path)
    monkeypatch.setenv("EIDOLON_DATA_SQLITE_PATH", str(data_db))
    app = _app(tmp_path)

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
    fake_map = {"r:alice:default": fake_proc, "ghost": FakeProc(pid=99002, create_time=0.0)}

    with (
        patch.object(runners_mod, "find_agent_processes", return_value=fake_map),
        patch.object(runners_mod, "find_consolidator_processes", return_value={}),
    ):
        async with await _http(app) as ac:
            resp = await ac.get("/api/memory/runners")

    data = resp.json()
    alice = next(r for r in data["runners"] if r["memory_realm_id"] == "r:alice:default")
    assert alice["pid"] == 99001
    assert alice["running"] is True
    assert [o["memory_realm_id"] for o in data["orphans"]] == ["ghost"]


async def test_endpoint_when_eidolon_data_missing(tmp_path, monkeypatch):
    data_db = _write_eidolon_data(tmp_path, missing=True)
    monkeypatch.setenv("EIDOLON_DATA_SQLITE_PATH", str(data_db))
    app = _app(tmp_path)

    with (
        patch.object(runners_mod, "find_agent_processes", return_value={}),
        patch.object(runners_mod, "find_consolidator_processes", return_value={}),
    ):
        async with await _http(app) as ac:
            resp = await ac.get("/api/memory/runners")

    data = resp.json()
    assert resp.status_code == 200
    assert data["realms_source_exists"] is False
    assert data["runners"] == []
