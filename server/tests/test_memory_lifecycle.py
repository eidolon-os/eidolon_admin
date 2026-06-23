"""Tests for legacy memory user lifecycle endpoints."""
from __future__ import annotations

import sqlite3
from pathlib import Path
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
def registry_db(tmp_path):
    db_path = tmp_path / "registry.sqlite3"
    palace = tmp_path / "alice_palace"
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
        conn.execute(
            """
            INSERT INTO users (
                user_id, enabled, palace_path, memory_port,
                consolidator_enabled, consolidator_interval_hours,
                consolidator_window_days, consolidator_min_drawers,
                consolidator_min_confidence
            ) VALUES ('alice', 1, ?, 8030, 1, 6.0, 30, 3, 0.6)
            """,
            (str(palace),),
        )
    conn.close()
    return db_path, palace


@pytest.fixture
def app(tmp_path, registry_db, monkeypatch):
    db_path, _palace = registry_db
    monkeypatch.setenv("EIDOLON_REGISTRY_DB_PATH", str(db_path))
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


async def test_init_creates_palace_dir(app, registry_db):
    _db_path, palace = registry_db
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
    assert palace.exists() and palace.is_dir()


async def test_init_unknown_user_404(app):
    async with await _http(app) as ac:
        resp = await ac.post("/api/memory/users/ghost/init")

    assert resp.status_code == 404
