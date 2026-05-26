"""Tests for consolidator user lifecycle endpoints."""
from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from eidolon_admin_server.app.main import create_app
from eidolon_admin_server.app.settings import AdminBindConfig, GatewayConfig, Settings


pytestmark = pytest.mark.asyncio


def _app(tmp_path: Path, users_yaml: Path):
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


async def test_put_consolidator_writes_yaml(tmp_path, monkeypatch):
    p = tmp_path / "users.yaml"
    p.write_text(textwrap.dedent("""\
        users:
          - id: alice
            port: 8030
            enabled: true
    """))
    monkeypatch.setenv("EIDOLON_MEMORY_USERS_YAML", str(p))
    app = _app(tmp_path, p)

    mock_sighup = AsyncMock(return_value={"signaled": False, "program": "memory:memory-supervisor"})
    with (
        patch(
            "eidolon_admin_server.app.memory.routers.lifecycle.sighup_memory_supervisor",
            mock_sighup,
        ),
        patch(
            "eidolon_admin_server.app.memory.routers.lifecycle.find_consolidator_processes",
            return_value={},
        ),
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://gw"
        ) as ac:
            resp = await ac.put(
                "/api/memory/users/alice/consolidator",
                json={
                    "enabled": True,
                    "interval_hours": 4,
                    "window_days": 14,
                    "min_drawers": 2,
                    "min_confidence": 0.7,
                },
            )

    assert resp.status_code == 200
    raw = p.read_text()
    assert "consolidator:" in raw
    assert "interval_hours: 4" in raw
    data = resp.json()
    assert data["user"]["consolidator"]["enabled"] is True


async def test_delete_consolidator_removes_block(tmp_path, monkeypatch):
    p = tmp_path / "users.yaml"
    p.write_text(textwrap.dedent("""\
        users:
          - id: alice
            port: 8030
            enabled: true
            consolidator:
              enabled: true
    """))
    monkeypatch.setenv("EIDOLON_MEMORY_USERS_YAML", str(p))
    app = _app(tmp_path, p)

    with (
        patch(
            "eidolon_admin_server.app.memory.routers.lifecycle.sighup_memory_supervisor",
            AsyncMock(return_value={"signaled": False}),
        ),
        patch(
            "eidolon_admin_server.app.memory.routers.lifecycle.find_consolidator_processes",
            return_value={},
        ),
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://gw"
        ) as ac:
            resp = await ac.delete("/api/memory/users/alice/consolidator")

    assert resp.status_code == 200
    assert "consolidator" not in p.read_text()
