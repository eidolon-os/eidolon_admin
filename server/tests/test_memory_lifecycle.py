"""Tests for memory user lifecycle endpoints (Phase 15).

Strategy: real users.yaml on tmp_path (we want the atomic-write paths to be
exercised), but supervisord SIGHUP + MCP probe are mocked. Lifecycle's job is
to orchestrate yaml → SIGHUP → wait; we verify that orchestration end-to-end.
"""
from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from eidolon_admin_server.app.main import create_app
from eidolon_admin_server.app.memory.runners import UserEntry
from eidolon_admin_server.app.memory.users_yaml import read_users
from eidolon_admin_server.app.settings import (
    AdminBindConfig,
    GatewayConfig,
    Settings,
)


pytestmark = pytest.mark.asyncio


@pytest.fixture
def users_yaml(tmp_path):
    p = tmp_path / "users.yaml"
    p.write_text(textwrap.dedent("""\
        users:
          - id: alice
            port: 8030
            enabled: true
            palace_path: ''
          - id: bob
            port: 8031
            enabled: false
            palace_path: ''
    """))
    return p


@pytest.fixture
def app(tmp_path, users_yaml, monkeypatch):
    monkeypatch.setenv("EIDOLON_MEMORY_USERS_YAML", str(users_yaml))
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


def _patch_supervisor(*, signaled=True, reachable=True):
    return (
        patch(
            "eidolon_admin_server.app.memory.routers.lifecycle.sighup_memory_supervisor",
            new=AsyncMock(return_value={"signaled": signaled, "program": "memory:memory-supervisor"}),
        ),
        patch(
            "eidolon_admin_server.app.memory.routers.lifecycle.wait_for_user_reachable",
            new=AsyncMock(return_value=reachable),
        ),
    )


# -- create -------------------------------------------------------------------


async def test_create_user_writes_yaml_and_signals(app, users_yaml):
    p_sig, p_wait = _patch_supervisor(signaled=True, reachable=True)
    with p_sig, p_wait:
        async with await _http(app) as ac:
            resp = await ac.post(
                "/api/memory/users",
                json={"id": "carol", "port": 8032, "enabled": True},
            )

    assert resp.status_code == 201
    body = resp.json()
    assert body["user"]["user_id"] == "carol"
    assert body["user"]["agent_reachable"] is True
    assert "memory:memory-supervisor" in body["message"]

    # yaml was actually mutated
    ids = [u.id for u in read_users(users_yaml)]
    assert ids == ["alice", "bob", "carol"]


async def test_create_duplicate_port_returns_409(app):
    p_sig, p_wait = _patch_supervisor()
    with p_sig, p_wait:
        async with await _http(app) as ac:
            resp = await ac.post(
                "/api/memory/users",
                json={"id": "dave", "port": 8030, "enabled": True},  # reuses alice's port
            )
    assert resp.status_code == 409
    assert "8030" in resp.json()["detail"]


# -- enable / disable / start / stop ------------------------------------------


@pytest.mark.parametrize("endpoint,expected_enabled,wait_reachable", [
    ("/api/memory/users/bob/enable?enabled=true", True, True),
    ("/api/memory/users/bob/start", True, True),
])
async def test_enable_paths(app, users_yaml, endpoint, expected_enabled, wait_reachable):
    p_sig, p_wait = _patch_supervisor(reachable=wait_reachable)
    with p_sig, p_wait:
        async with await _http(app) as ac:
            resp = await ac.post(endpoint)
    assert resp.status_code == 200
    assert resp.json()["user"]["enabled"] is expected_enabled
    # yaml flips bob.enabled
    bob = next(u for u in read_users(users_yaml) if u.id == "bob")
    assert bob.enabled is expected_enabled


@pytest.mark.parametrize("endpoint", [
    "/api/memory/users/alice/enable?enabled=false",
    "/api/memory/users/alice/stop",
])
async def test_disable_paths(app, users_yaml, endpoint):
    """Stopping a user doesn't wait for the agent to come back up — that
    would never finish."""
    p_sig, p_wait = _patch_supervisor()
    with p_sig, p_wait:
        async with await _http(app) as ac:
            resp = await ac.post(endpoint)
    assert resp.status_code == 200
    assert resp.json()["user"]["enabled"] is False
    alice = next(u for u in read_users(users_yaml) if u.id == "alice")
    assert alice.enabled is False


async def test_enable_unknown_user_404(app):
    p_sig, p_wait = _patch_supervisor()
    with p_sig, p_wait:
        async with await _http(app) as ac:
            resp = await ac.post("/api/memory/users/ghost/enable?enabled=true")
    assert resp.status_code == 404


# -- init palace --------------------------------------------------------------


async def test_init_creates_palace_dir(app, users_yaml, tmp_path):
    palace = tmp_path / "alice_palace"
    # Update alice's palace_path via direct yaml manipulation to mirror real setup.
    p = users_yaml
    p.write_text(textwrap.dedent(f"""\
        users:
          - id: alice
            port: 8030
            enabled: true
            palace_path: '{palace}'
    """))

    p_sig, p_wait = _patch_supervisor()
    with p_sig, p_wait:
        async with await _http(app) as ac:
            resp = await ac.post("/api/memory/users/alice/init")

    assert resp.status_code == 200
    assert palace.exists() and palace.is_dir()


async def test_init_unknown_user_404(app):
    p_sig, p_wait = _patch_supervisor()
    with p_sig, p_wait:
        async with await _http(app) as ac:
            resp = await ac.post("/api/memory/users/ghost/init")
    assert resp.status_code == 404


# -- supervisor down ----------------------------------------------------------


async def test_create_user_when_supervisor_down_still_succeeds(app, users_yaml):
    """yaml write must succeed even if memory-supervisor isn't running.

    The caller can re-trigger reconciliation later; not having the supervisor
    up shouldn't reject user-management.
    """
    p_sig, p_wait = _patch_supervisor(signaled=False, reachable=False)
    with p_sig, p_wait:
        async with await _http(app) as ac:
            resp = await ac.post(
                "/api/memory/users",
                json={"id": "frank", "port": 8040, "enabled": True},
            )
    assert resp.status_code == 201
    body = resp.json()
    assert body["user"]["agent_reachable"] is False
    # yaml still got the user
    assert any(u.id == "frank" for u in read_users(users_yaml))
