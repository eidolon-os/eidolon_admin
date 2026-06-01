"""Tests for /api/bootstrap/state — Phase 29.J.

Bootstrap is a tiny aggregator with one job: tell the frontend which
onboarding step the operator is on, never raise. The tests pin both
the happy "everything ready" path and the failure-tolerance contract.
"""
from __future__ import annotations

from typing import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI

from eidolon_admin_server.app.routers.bootstrap import router as bootstrap_router


class _FakeOrch:
    """Minimal stand-in for an entity orchestrator's list method.

    We only need ``len()`` over the returned list; the real types
    don't matter for the bootstrap counter.
    """

    def __init__(self, method_name: str, rows: list | Exception):
        self._method = method_name
        self._rows = rows
        setattr(self, method_name, self._call)

    async def _call(self, *args, **kwargs):
        if isinstance(self._rows, Exception):
            raise self._rows
        return self._rows


def _build_app(
    tenant: _FakeOrch | None,
    template: _FakeOrch | None,
    user: _FakeOrch | None,
    agent: _FakeOrch | None,
    device: _FakeOrch | None,
) -> FastAPI:
    app = FastAPI()
    app.include_router(bootstrap_router, prefix="/api")
    app.state.tenant_orchestrator = tenant
    app.state.template_orchestrator = template
    app.state.user_orchestrator = user
    app.state.agent_orchestrator = agent
    app.state.device_orchestrator = device
    return app


@pytest.fixture
async def client_factory():
    async def _build(**kwargs) -> AsyncIterator[httpx.AsyncClient]:
        app = _build_app(**kwargs)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield client

    return _build


@pytest.mark.asyncio
async def test_all_orchestrators_missing_yields_unknown():
    """No orchestrators wired (e.g. NATS down during boot) → every step
    reports ``unknown`` and ``ready`` is False, but the endpoint
    itself returns 200. The point: the probe never blocks the page."""
    app = _build_app(None, None, None, None, None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.get("/api/bootstrap/state")
    assert r.status_code == 200
    data = r.json()
    assert data["ready"] is False
    assert data["next_step"] is None  # no "empty" step to surface
    for key in ("tenants", "templates", "users", "agents", "devices"):
        assert data[key]["status"] == "unknown"
        assert data[key]["count"] == 0


@pytest.mark.asyncio
async def test_fresh_install_points_at_templates():
    """Tenant seeded, everything else empty → ``next_step=templates``
    because templates is the first non-ok step after tenants."""
    app = _build_app(
        tenant=_FakeOrch("list_all", [{"tenant_id": "default"}]),
        template=_FakeOrch("list_all", []),
        user=_FakeOrch("list_users", []),
        agent=_FakeOrch("list_agents", []),
        device=_FakeOrch("list_devices", []),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.get("/api/bootstrap/state")
    data = r.json()
    assert data["tenants"] == {"status": "ok", "count": 1}
    assert data["templates"]["status"] == "empty"
    assert data["next_step"] == "templates"
    assert data["ready"] is False


@pytest.mark.asyncio
async def test_all_ok_yields_ready():
    """Every step has at least one row → ready=True, no next_step."""
    app = _build_app(
        tenant=_FakeOrch("list_all", [{"tenant_id": "default"}]),
        template=_FakeOrch("list_all", [{"id": "t1"}]),
        user=_FakeOrch("list_users", [{"id": "alice"}]),
        agent=_FakeOrch("list_agents", [{"id": "a1"}]),
        device=_FakeOrch("list_devices", [{"id": "d1"}]),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.get("/api/bootstrap/state")
    data = r.json()
    assert data["ready"] is True
    assert data["next_step"] is None
    for k in ("tenants", "templates", "users", "agents", "devices"):
        assert data[k]["status"] == "ok"
        assert data[k]["count"] == 1


@pytest.mark.asyncio
async def test_orchestrator_raises_becomes_unknown_not_500():
    """If an orchestrator's list method blows up (memory down mid-probe,
    say), that step is ``unknown`` — the rest still report. The whole
    point: bootstrap NEVER 500s."""
    app = _build_app(
        tenant=_FakeOrch("list_all", [{"tenant_id": "default"}]),
        template=_FakeOrch("list_all", [{"id": "t1"}]),
        user=_FakeOrch("list_users", RuntimeError("memory down")),
        agent=_FakeOrch("list_agents", []),
        device=_FakeOrch("list_devices", []),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.get("/api/bootstrap/state")
    assert r.status_code == 200
    data = r.json()
    assert data["users"]["status"] == "unknown"
    # Unknown is NOT counted as empty, so next_step jumps to agents.
    assert data["next_step"] == "agents"
    assert data["ready"] is False


@pytest.mark.asyncio
async def test_unknown_steps_do_not_block_ready():
    """Mid-step ``unknown`` shouldn't be the ``next_step`` — the
    operator can't act on "memory was momentarily flaky". But it also
    can't be ``ok``, so ready stays False."""
    app = _build_app(
        tenant=_FakeOrch("list_all", [{"tenant_id": "default"}]),
        template=_FakeOrch("list_all", [{"id": "t1"}]),
        user=_FakeOrch("list_users", RuntimeError("blip")),
        agent=_FakeOrch("list_agents", [{"id": "a1"}]),
        device=_FakeOrch("list_devices", [{"id": "d1"}]),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.get("/api/bootstrap/state")
    data = r.json()
    assert data["ready"] is False
    assert data["next_step"] is None  # nothing "empty" exists to point at
