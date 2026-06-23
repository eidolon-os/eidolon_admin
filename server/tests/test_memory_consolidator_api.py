"""Tests for legacy memory consolidator lifecycle endpoints."""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from eidolon_admin_server.app.main import create_app
from eidolon_admin_server.app.settings import AdminBindConfig, GatewayConfig, Settings


pytestmark = pytest.mark.asyncio


def _app(tmp_path: Path):
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


async def test_put_consolidator_rejected(tmp_path):
    app = _app(tmp_path)
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

    assert resp.status_code == 409
    assert "memory no longer owns consolidator" in resp.json()["detail"]


async def test_delete_consolidator_rejected(tmp_path):
    app = _app(tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gw"
    ) as ac:
        resp = await ac.delete("/api/memory/users/alice/consolidator")

    assert resp.status_code == 409
    assert "memory no longer owns consolidator" in resp.json()["detail"]
