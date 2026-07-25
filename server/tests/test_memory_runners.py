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
from eidolon_sdk.memory import memory_space_storage_name

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
                    json.dumps({"consolidator": {"enabled": True}}),
                ),
                (
                    "bob",
                    "Bob",
                    json.dumps({"registry": {"enabled": True}}),
                    json.dumps({"consolidator": {"enabled": True}}),
                ),
                (
                    "disabled-carol",
                    "Carol",
                    json.dumps({"registry": {"enabled": False}}),
                    json.dumps({"consolidator": {"enabled": True}}),
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
    assert (
        f(["x", "--memory-space-id", "r:alice:default", "--port", "8030"])
        == "r:alice:default"
    )
    assert f(["x", "--memory-space-id=default.bob.default"]) == "default.bob.default"
    assert f(["x", "--port", "8030"]) is None


async def test_inspect_palace_backend_distinguishes_empty_stale_artifact(
    tmp_path: Path,
):
    chroma = sqlite3.connect(tmp_path / "chroma.sqlite3")
    with chroma:
        chroma.execute("CREATE TABLE collections (id TEXT)")
        chroma.execute("CREATE TABLE embeddings (id TEXT)")
    chroma.close()
    (tmp_path / "sqlite_exact.sqlite3").touch()

    report = runners_mod.inspect_palace_backend(
        tmp_path,
        configured_backend="chroma",
    )

    assert report["backend_state"] == "stale_artifact"
    assert report["configured_backend"] == "chroma"
    by_backend = {item["backend"]: item for item in report["backend_artifacts"]}
    assert by_backend["chroma"]["state"] == "valid"
    assert by_backend["sqlite_exact"]["state"] == "invalid"
    assert by_backend["sqlite_exact"]["detail"] == "empty artifact"


async def _http(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gw"
    )


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
    assert all(r["port"] > 0 for r in data["runners"])
    assert len({r["port"] for r in data["runners"]}) == 3
    # None of them are running.
    assert all(r["pid"] is None and r["running"] is False for r in data["runners"])
    assert all(r["consolidator"]["running"] is False for r in data["runners"])
    assert data["consolidator_orphans"] == []
    # Disabled user is not probed.
    carol = next(
        r for r in data["runners"] if r["memory_realm_id"] == "r:carol:default"
    )
    assert carol["listening"] is False
    assert data["orphans"] == []


async def test_load_realms_derives_routes_when_engine_config_is_empty(
    tmp_path, monkeypatch
):
    data_db = _write_eidolon_data(tmp_path)
    memory_settings = tmp_path / "memory-settings.yaml"
    memory_settings.write_text(
        "runtime:\n"
        f"  palaces_root: {tmp_path / 'mempalaces'}\n"
        "mempalace:\n"
        "  backend: chroma\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("EIDOLON_DATA_SQLITE_PATH", str(data_db))
    monkeypatch.setenv("EIDOLON_MEMORY_MCP_PORT", "8030")
    monkeypatch.setenv("EIDOLON_MEMORY_SETTINGS_YAML", str(memory_settings))

    realms = runners_mod.load_realms()

    alice = next(r for r in realms if r.memory_realm_id == "r:alice:default")
    assert alice.port > 0
    assert alice.mcp_http_url == f"http://127.0.0.1:{alice.port}/mcp"
    assert alice.port != 0
    assert alice.palace_path == str(
        tmp_path / "mempalaces" / memory_space_storage_name("r:alice:default")
    )
    assert alice.configured_backend == "chroma"


async def test_load_realms_uses_global_backend_not_realm_override(
    tmp_path, monkeypatch
):
    data_db = _write_eidolon_data(tmp_path)
    memory_settings = tmp_path / "memory-settings.yaml"
    memory_settings.write_text(
        "mempalace:\n"
        "  backend: qdrant\n",
        encoding="utf-8",
    )
    conn = sqlite3.connect(data_db)
    with conn:
        conn.execute(
            "UPDATE memory_realms SET engine_config_json = ?",
            (json.dumps({"backend": "sqlite_exact"}),),
        )
    conn.close()
    monkeypatch.setenv("EIDOLON_DATA_SQLITE_PATH", str(data_db))
    monkeypatch.setenv("EIDOLON_MEMORY_SETTINGS_YAML", str(memory_settings))

    realms = runners_mod.load_realms()

    assert {realm.configured_backend for realm in realms} == {"qdrant"}


async def test_load_realms_ignores_legacy_palace_path_overrides(tmp_path, monkeypatch):
    data_db = _write_eidolon_data(tmp_path)
    monkeypatch.setenv("EIDOLON_DATA_SQLITE_PATH", str(data_db))
    monkeypatch.setenv("EIDOLON_MEMORY_PALACES_ROOT", str(tmp_path / "runtime-root"))
    conn = sqlite3.connect(data_db)
    with conn:
        conn.execute(
            "UPDATE owners SET settings_json = ? WHERE owner_id = 'alice'",
            (json.dumps({"palace_path": "/tmp/owner-palace"}),),
        )
        conn.execute(
            "UPDATE memory_realms SET engine_config_json = ? WHERE realm_id = 'r:alice:default'",
            (json.dumps({"palace_path": "/tmp/realm-palace"}),),
        )
    conn.close()

    realms = runners_mod.load_realms()

    alice = next(r for r in realms if r.memory_realm_id == "r:alice:default")
    assert alice.palace_path == str(
        tmp_path / "runtime-root" / memory_space_storage_name("r:alice:default")
    )


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
    fake_map = {
        "r:alice:default": fake_proc,
        "ghost": FakeProc(pid=99002, create_time=0.0),
    }

    with (
        patch.object(runners_mod, "find_agent_processes", return_value=fake_map),
        patch.object(runners_mod, "find_consolidator_processes", return_value={}),
    ):
        async with await _http(app) as ac:
            resp = await ac.get("/api/memory/runners")

    data = resp.json()
    alice = next(
        r for r in data["runners"] if r["memory_realm_id"] == "r:alice:default"
    )
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
