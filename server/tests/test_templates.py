"""Tests for admin's Templates module (Phase 29.D).

Two layers under test:

  - **TemplateAgentClient**: raw HTTP wrapper. Uses respx to mock agent's
    REST surface so we cover the transport translation (connection
    errors, 4xx, 5xx, 2xx).

  - **TemplateOrchestrator**: translates agent's wire format to admin's
    schema, maps status codes to admin's exception classes.

  - **Router**: end-to-end via httpx.ASGITransport + respx, covering the
    HTTP status codes for each error class + the "upstream available"
    envelope flag.

We use respx rather than a real agent because:
  - we already test agent's side of the contract in eidolon_agent's
    test_router_templates (real SQL + real registry)
  - admin's layer is pure translation — the value is in pinning the
    error mapping, not in re-running agent's CRUD
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import AsyncIterator

import httpx
import pytest
import respx
from eidolon_sdk.http import ServiceUnavailable, ServiceUpstreamError
from fastapi import FastAPI

from eidolon_admin_server.app.registry.templates import (
    TemplateAgentClient,
    TemplateOrchestrator,
    router as templates_router,
)
from eidolon_admin_server.app.registry.templates.orchestrator import (
    TemplateAgentDown,
    TemplateConflict,
    TemplateInvalid,
    TemplateNotFound,
)
from eidolon_admin_server.app.registry.schemas.template import (
    CreateTemplateRequest,
    ForkTemplateRequest,
    UpdateTemplateRequest,
)


AGENT_URL = "http://agent.test"


# ---- TemplateAgentClient: HTTP wrapper ------------------------------------


@pytest.fixture
async def http_client() -> AsyncIterator[httpx.AsyncClient]:
    """Shared async client matching production's ``app.state.http_client``."""
    async with httpx.AsyncClient() as c:
        yield c


@pytest.fixture
def agent_client(http_client: httpx.AsyncClient) -> TemplateAgentClient:
    return TemplateAgentClient(http_client, AGENT_URL)


async def test_client_list_returns_decoded_summaries(
    agent_client: TemplateAgentClient,
) -> None:
    with respx.mock(base_url=AGENT_URL) as rsx:
        rsx.get("/api/admin/personas/templates").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "template_id": "alpha",
                        "template_revision": 1,
                        "name": "Alpha",
                        "archetype": "caretaker",
                        "description": "",
                    }
                ],
            )
        )
        result = await agent_client.list_templates()
    assert result == [
        {
            "template_id": "alpha",
            "template_revision": 1,
            "name": "Alpha",
            "archetype": "caretaker",
            "description": "",
        }
    ]


async def test_client_connection_refused_raises_unreachable(
    agent_client: TemplateAgentClient,
) -> None:
    with respx.mock(base_url=AGENT_URL) as rsx:
        rsx.get("/api/admin/personas/templates").mock(
            side_effect=httpx.ConnectError("refused")
        )
        with pytest.raises(ServiceUnavailable):
            await agent_client.list_templates()


async def test_client_404_raises_upstream_error(
    agent_client: TemplateAgentClient,
) -> None:
    with respx.mock(base_url=AGENT_URL) as rsx:
        rsx.get("/api/admin/personas/templates/ghost").mock(
            return_value=httpx.Response(404, text="not found")
        )
        with pytest.raises(ServiceUpstreamError) as exc_info:
            await agent_client.get_template("ghost")
    assert exc_info.value.status_code == 404


async def test_client_500_raises_upstream_error(
    agent_client: TemplateAgentClient,
) -> None:
    with respx.mock(base_url=AGENT_URL) as rsx:
        rsx.get("/api/admin/personas/templates").mock(
            return_value=httpx.Response(500, text="server boom")
        )
        with pytest.raises(ServiceUpstreamError) as exc_info:
            await agent_client.list_templates()
    assert exc_info.value.status_code == 500


# ---- TemplateOrchestrator: status code translation ------------------------


@pytest.fixture
def orchestrator(agent_client: TemplateAgentClient) -> TemplateOrchestrator:
    return TemplateOrchestrator(agent_client)


async def test_orchestrator_translates_404_to_template_not_found(
    orchestrator: TemplateOrchestrator,
) -> None:
    with respx.mock(base_url=AGENT_URL) as rsx:
        rsx.get("/api/admin/personas/templates/ghost").mock(
            return_value=httpx.Response(404)
        )
        with pytest.raises(TemplateNotFound):
            await orchestrator.get("ghost")


async def test_orchestrator_translates_409_to_template_conflict(
    orchestrator: TemplateOrchestrator,
) -> None:
    with respx.mock(base_url=AGENT_URL) as rsx:
        rsx.post("/api/admin/personas/templates").mock(
            return_value=httpx.Response(409, text="builtin id")
        )
        with pytest.raises(TemplateConflict):
            await orchestrator.create(
                CreateTemplateRequest(
                    template_id="builtin_one",
                    tenant_id="default",
                    display_name="x",
                    yaml_body="a: 1",
                )
            )


async def test_orchestrator_translates_422_to_template_invalid(
    orchestrator: TemplateOrchestrator,
) -> None:
    with respx.mock(base_url=AGENT_URL) as rsx:
        rsx.post("/api/admin/personas/templates").mock(
            return_value=httpx.Response(422, text="bad yaml")
        )
        with pytest.raises(TemplateInvalid):
            await orchestrator.create(
                CreateTemplateRequest(
                    template_id="bad_t",
                    tenant_id="default",
                    display_name="x",
                    yaml_body="not yaml",
                )
            )


async def test_orchestrator_unreachable_agent_translates_to_agent_down(
    orchestrator: TemplateOrchestrator,
) -> None:
    with respx.mock(base_url=AGENT_URL) as rsx:
        rsx.get("/api/admin/personas/templates").mock(
            side_effect=httpx.ConnectError("agent down")
        )
        with pytest.raises(TemplateAgentDown):
            await orchestrator.list_all()


async def test_orchestrator_create_builds_correct_template_ref(
    orchestrator: TemplateOrchestrator,
) -> None:
    """Round-trip: send a CreateTemplateRequest, agent responds with
    the row shape, orchestrator must return a valid TemplateRef.
    """
    now = datetime.now(timezone.utc).isoformat()
    with respx.mock(base_url=AGENT_URL) as rsx:
        rsx.post("/api/admin/personas/templates").mock(
            return_value=httpx.Response(
                201,
                json={
                    "template_id": "my_t",
                    "tenant_id": "default",
                    "display_name": "My T",
                    "archetype": "custom",
                    "yaml_body": "a: 1",
                    "revision": 1,
                    "created_at": now,
                    "updated_at": now,
                },
            )
        )
        ref = await orchestrator.create(
            CreateTemplateRequest(
                template_id="my_t",
                tenant_id="default",
                display_name="My T",
                yaml_body="a: 1",
            )
        )
    assert ref.template_id == "my_t"
    assert ref.tenant_id == "default"
    assert ref.source == "custom"
    assert ref.revision == 1
    assert ref.display_name == "My T"


async def test_orchestrator_fork_round_trip(
    orchestrator: TemplateOrchestrator,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with respx.mock(base_url=AGENT_URL) as rsx:
        rsx.post("/api/admin/personas/templates/source/fork").mock(
            return_value=httpx.Response(
                201,
                json={
                    "template_id": "forked",
                    "tenant_id": "default",
                    "display_name": "Forked",
                    "archetype": "caretaker",
                    "yaml_body": "...",
                    "revision": 1,
                    "created_at": now,
                    "updated_at": now,
                },
            )
        )
        ref = await orchestrator.fork(
            "source",
            ForkTemplateRequest(
                new_template_id="forked",
                target_tenant_id="default",
                new_display_name="Forked",
            ),
        )
    assert ref.template_id == "forked"
    assert ref.archetype == "caretaker"


# ---- Router: HTTP status code mapping --------------------------------------


@pytest.fixture
async def client(
    orchestrator: TemplateOrchestrator,
) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.state.template_orchestrator = orchestrator
    app.include_router(templates_router, prefix="/api")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", trust_env=False
    ) as c:
        yield c


async def test_http_list_returns_upstream_available_true(
    client: httpx.AsyncClient,
) -> None:
    with respx.mock(base_url=AGENT_URL) as rsx:
        rsx.get("/api/admin/personas/templates").mock(
            return_value=httpx.Response(200, json=[])
        )
        r = await client.get("/api/templates")
    assert r.status_code == 200
    assert r.json() == {"templates": [], "upstream_available": True}


async def test_http_list_returns_envelope_when_agent_down(
    client: httpx.AsyncClient,
) -> None:
    """Critical UX rule: if agent is unreachable, list endpoint still
    returns 200 with empty list + upstream_available=False — the UI
    banners "agent unavailable" rather than crashing on a 503."""
    with respx.mock(base_url=AGENT_URL) as rsx:
        rsx.get("/api/admin/personas/templates").mock(
            side_effect=httpx.ConnectError("down")
        )
        r = await client.get("/api/templates")
    assert r.status_code == 200
    assert r.json() == {"templates": [], "upstream_available": False}


async def test_http_get_404_propagates(client: httpx.AsyncClient) -> None:
    with respx.mock(base_url=AGENT_URL) as rsx:
        rsx.get("/api/admin/personas/templates/ghost").mock(
            return_value=httpx.Response(404)
        )
        r = await client.get("/api/templates/ghost")
    assert r.status_code == 404


async def test_http_create_409_propagates(client: httpx.AsyncClient) -> None:
    with respx.mock(base_url=AGENT_URL) as rsx:
        rsx.post("/api/admin/personas/templates").mock(
            return_value=httpx.Response(409, text="builtin")
        )
        r = await client.post(
            "/api/templates",
            json={
                "template_id": "builtin_one",
                "tenant_id": "default",
                "display_name": "x",
                "yaml_body": "a: 1",
            },
        )
    assert r.status_code == 409


async def test_http_delete_204(client: httpx.AsyncClient) -> None:
    with respx.mock(base_url=AGENT_URL) as rsx:
        rsx.delete("/api/admin/personas/templates/my_t").mock(
            return_value=httpx.Response(204)
        )
        r = await client.delete("/api/templates/my_t")
    assert r.status_code == 204


async def test_http_503_when_orchestrator_missing() -> None:
    app = FastAPI()
    app.state.template_orchestrator = None
    app.include_router(templates_router, prefix="/api")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/api/templates")
    assert r.status_code == 503


async def test_http_fork_201(client: httpx.AsyncClient) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with respx.mock(base_url=AGENT_URL) as rsx:
        rsx.post("/api/admin/personas/templates/src/fork").mock(
            return_value=httpx.Response(
                201,
                json={
                    "template_id": "fork1",
                    "tenant_id": "default",
                    "display_name": "Fork1",
                    "archetype": "caretaker",
                    "yaml_body": "...",
                    "revision": 1,
                    "created_at": now,
                    "updated_at": now,
                },
            )
        )
        r = await client.post(
            "/api/templates/src/fork",
            json={
                "new_template_id": "fork1",
                "target_tenant_id": "default",
                "new_display_name": "Fork1",
            },
        )
    assert r.status_code == 201
    assert r.json()["template_id"] == "fork1"
