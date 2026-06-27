"""Tests for legacy memory user lifecycle endpoints."""
from __future__ import annotations

import json
import sqlite3
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from eidolon_admin_server.app.main import create_app
from eidolon_admin_server.app.settings import (
    AdminBindConfig,
    GatewayConfig,
    Settings,
)


pytestmark = pytest.mark.asyncio


@pytest.fixture
def palace_dir(tmp_path):
    return tmp_path / "alice_palace"


@pytest.fixture
def eidolon_data_db(tmp_path, palace_dir):
    db_path = tmp_path / "eidolon.sqlite3"
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
            INSERT INTO owners (
                owner_id, display_name, kind, profile_json, settings_json
            ) VALUES ('alice', 'Alice', 'person', ?, ?)
            """,
            (
                json.dumps({"registry": {"enabled": True}}),
                json.dumps({"memory_port": 8030, "palace_path": str(palace_dir)}),
            ),
        )
    conn.close()
    return db_path


@pytest.fixture
def app(tmp_path, eidolon_data_db, monkeypatch):
    monkeypatch.setenv("EIDOLON_DATA_SQLITE_PATH", str(eidolon_data_db))
    settings = Settings(
        services_file=tmp_path / "svc.yaml",
        supervisor_socket=tmp_path / "missing.sock",
        supervisor_available_dir=tmp_path,
        supervisor_enabled_dir=tmp_path,
    )
    (tmp_path / "svc.yaml").write_text("services: []\n")
    return create_app(
        GatewayConfig(admin=AdminBindConfig(cors_origins=[]), services=[]),
        settings=settings,
    )


async def _http(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw")


async def test_create_user_rejected(app):
    async with await _http(app) as ac:
        resp = await ac.post(
            "/api/memory/users",
            json={"id": "carol", "port": 8032, "enabled": True},
        )

    assert resp.status_code == 409
    assert "memory no longer owns users" in resp.json()["detail"]


@pytest.mark.parametrize("endpoint", [
    "/api/memory/users/alice/enable?enabled=false",
    "/api/memory/users/alice/start",
    "/api/memory/users/alice/stop",
])
async def test_enable_start_stop_rejected(app, endpoint):
    async with await _http(app) as ac:
        resp = await ac.post(endpoint)

    assert resp.status_code == 409
    assert "memory no longer owns enabled" in resp.json()["detail"]


async def test_init_creates_palace_dir(app, palace_dir):
    with (
        patch(
            "eidolon_admin_server.app.memory.routers.lifecycle.sighup_memory_supervisor",
            new=AsyncMock(return_value={"signaled": False}),
        ),
        patch(
            "eidolon_admin_server.app.memory.routers.lifecycle.wait_for_user_reachable",
            new=AsyncMock(return_value=False),
        ),
    ):
        async with await _http(app) as ac:
            resp = await ac.post("/api/memory/users/alice/init")

    assert resp.status_code == 200
    assert palace_dir.exists() and palace_dir.is_dir()


async def test_init_unknown_user_404(app):
    async with await _http(app) as ac:
        resp = await ac.post("/api/memory/users/ghost/init")

    assert resp.status_code == 404
