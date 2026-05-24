"""HTTP-shape tests for /api/devices/* — the Browser ↔ admin router edge.

These build the real FastAPI app (``create_app``), wire a real
:class:`DeviceOrchestrator` into ``app.state`` against real NATS + respx-
mocked hub/agent, and drive it through ``httpx.AsyncClient(transport=...)``
against the ASGI surface directly. We avoid ``starlette.testclient.TestClient``
because it spins up its own event loop, which conflicts with the
pytest-asyncio loop that owns our ``KVClient`` and NATS connection.

This test file covers what unit tests on the orchestrator cannot:

- Pydantic request validation (missing / empty fields → 422)
- ``OrchestratorError`` subclass → HTTP status code mapping
- 503 degradation path when ``app.state.device_orchestrator`` is None
- response envelope shape matches the documented schemas

Each test name spells out the contract being verified.
"""
from __future__ import annotations

import uuid
from typing import AsyncIterator

import httpx
import pytest
import respx

from eidolon_admin_server.app.devices.orchestrator import DeviceOrchestrator
from eidolon_admin_server.app.devices.repository import (
    AGENTS_BUCKET,
    MAPPINGS_BUCKET,
    MAX_SOUL_SIZE_BYTES,
    SOULS_BUCKET,
    DeviceBindingRepository,
)
from eidolon_admin_server.app.main import create_app
from eidolon_admin_server.app.nats_kv import KVClient
from eidolon_admin_server.app.settings import (
    AdminBindConfig,
    AuthConfig,
    GatewayConfig,
    ServiceConfig,
)


HUB_URL = "http://hub.test"
AGENT_URL = "http://agent.test"


# ---- helpers --------------------------------------------------------------


def _gateway_cfg() -> GatewayConfig:
    """A minimal services.yaml-equivalent registry pointing at respx hosts.

    The orchestrator reads service base_urls out of this config — keeping
    test routing out of env vars / file fixtures.
    """
    return GatewayConfig(
        admin=AdminBindConfig(host="127.0.0.1", port=9000, cors_origins=[]),
        services=[
            ServiceConfig(
                id="hub",
                name="Hub",
                base_url=HUB_URL,
                upstream_prefix="/api/admin",
                auth=AuthConfig(type="none"),
                features=[],
            ),
            ServiceConfig(
                id="agent",
                name="Agent",
                base_url=AGENT_URL,
                upstream_prefix="/api/admin",
                auth=AuthConfig(type="none"),
                features=[],
            ),
        ],
    )


def _device_payload(device_id: str, approved: bool = True) -> dict:
    return {
        "device_id": device_id,
        "name": "tester",
        "enabled": True,
        "paired": False,
        "approved": approved,
        "approved_at": "2026-05-25T00:00:00+00:00" if approved else None,
        "last_seen": None,
        "status": "offline",
        "room_name": "",
        "participant_sid": "",
        "missed_probes": 0,
    }


# ---- fixtures -------------------------------------------------------------


@pytest.fixture
async def kv_client() -> AsyncIterator[KVClient]:
    """Real NATS — skip whole file if unreachable."""
    client = KVClient()
    try:
        await client.connect()
    except Exception:
        pytest.skip("NATS not reachable at 127.0.0.1:4222")
    yield client
    await client.close()


@pytest.fixture
async def http_app(kv_client: KVClient):
    """Build the real app and wire a real orchestrator pointed at respx hosts.

    Each test gets unique bucket names so concurrent runs don't trample
    each other's state. We mutate the frozen ``BucketSpec`` dataclasses
    via ``object.__setattr__`` for the test's duration, restoring at the
    end — the repository imports these as module-level singletons.
    """
    suffix = uuid.uuid4().hex[:10]
    original_names = (MAPPINGS_BUCKET.name, SOULS_BUCKET.name, AGENTS_BUCKET.name)
    object.__setattr__(MAPPINGS_BUCKET, "name", f"test_router_map_{suffix}")
    object.__setattr__(SOULS_BUCKET, "name", f"test_router_souls_{suffix}")
    object.__setattr__(AGENTS_BUCKET, "name", f"test_router_agents_{suffix}")

    app = create_app(_gateway_cfg())
    repo = DeviceBindingRepository(kv_client)
    await repo.ensure_buckets()
    app.state.device_orchestrator = DeviceOrchestrator(
        repo=repo,
        http_client=app.state.http_client,
        hub_base_url=HUB_URL,
        agent_base_url=AGENT_URL,
    )

    yield app

    await app.state.http_client.aclose()
    object.__setattr__(MAPPINGS_BUCKET, "name", original_names[0])
    object.__setattr__(SOULS_BUCKET, "name", original_names[1])
    object.__setattr__(AGENTS_BUCKET, "name", original_names[2])


@pytest.fixture
async def client(http_app) -> AsyncIterator[httpx.AsyncClient]:
    """In-process ASGI client. No socket, no separate event loop."""
    transport = httpx.ASGITransport(app=http_app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", trust_env=False
    ) as c:
        yield c


# ---- list endpoint --------------------------------------------------------


@pytest.mark.asyncio
async def test_list_devices_fuses_hub_and_nats_into_unified_envelope(
    client: httpx.AsyncClient,
) -> None:
    """GET /api/devices returns hub rows + per-device NATS binding."""
    with respx.mock:
        respx.get(f"{HUB_URL}/api/admin/devices").mock(
            return_value=httpx.Response(200, json={"devices": [_device_payload("d1")]})
        )
        resp = await client.get("/api/devices")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["nats_available"] is True
    assert len(body["devices"]) == 1
    dev = body["devices"][0]
    assert dev["device_id"] == "d1"
    assert dev["approved"] is True
    assert dev["binding"] is None  # nothing bound yet


@pytest.mark.asyncio
async def test_list_devices_returns_503_when_orchestrator_unset(
    http_app, client: httpx.AsyncClient,
) -> None:
    """If NATS was down at boot, the orchestrator stays None — router 503s.

    This is the documented degradation contract: admin keeps running, the
    devices feature reports a clear unavailable state instead of cascading
    into a generic 500.
    """
    http_app.state.device_orchestrator = None
    resp = await client.get("/api/devices")
    assert resp.status_code == 503
    assert "NATS" in resp.json()["detail"]


# ---- approve endpoint -----------------------------------------------------


@pytest.mark.asyncio
async def test_approve_passes_through_to_hub(client: httpx.AsyncClient) -> None:
    with respx.mock:
        respx.post(f"{HUB_URL}/api/admin/devices/d1/approve").mock(
            return_value=httpx.Response(200, json={
                "device_id": "d1",
                "approved": True,
                "approved_at": "2026-05-25T00:00:00+00:00",
            })
        )
        resp = await client.post("/api/devices/d1/approve")
    assert resp.status_code == 200
    body = resp.json()
    assert body["approved"] is True


@pytest.mark.asyncio
async def test_approve_unknown_device_surfaces_as_404(client: httpx.AsyncClient) -> None:
    """Hub's 404 must reach the operator as a 404, not a 500."""
    with respx.mock:
        respx.post(f"{HUB_URL}/api/admin/devices/ghost/approve").mock(
            return_value=httpx.Response(404, json={"detail": "not registered"})
        )
        resp = await client.post("/api/devices/ghost/approve")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_approve_hub_unreachable_surfaces_as_503(client: httpx.AsyncClient) -> None:
    with respx.mock:
        respx.post(f"{HUB_URL}/api/admin/devices/d1/approve").mock(
            side_effect=httpx.ConnectError("connection refused")
        )
        resp = await client.post("/api/devices/d1/approve")
    assert resp.status_code == 503


# ---- create agent endpoint ------------------------------------------------


@pytest.mark.asyncio
async def test_create_agent_returns_201ish_shape_and_persists(
    client: httpx.AsyncClient,
) -> None:
    """The happy path produces a CreateAgentResponse with usable fields."""
    with respx.mock:
        respx.get(f"{HUB_URL}/api/admin/devices").mock(
            return_value=httpx.Response(200, json={"devices": [_device_payload("d1")]})
        )
        respx.post(f"{AGENT_URL}/api/admin/personas/templates/T/render").mock(
            return_value=httpx.Response(200, json={
                "markdown": "# soul\n",
                "template_id": "T",
                "template_revision": 1,
            })
        )
        resp = await client.post(
            "/api/devices/d1/agents",
            json={"template_id": "T", "user_id": "alice"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["is_active"] is True
    assert body["soul_preview_chars"] == len("# soul\n")
    assert body["agent_id"]


@pytest.mark.asyncio
async def test_create_agent_unapproved_device_returns_409(
    client: httpx.AsyncClient,
) -> None:
    with respx.mock:
        respx.get(f"{HUB_URL}/api/admin/devices").mock(
            return_value=httpx.Response(
                200, json={"devices": [_device_payload("d1", approved=False)]}
            )
        )
        resp = await client.post(
            "/api/devices/d1/agents",
            json={"template_id": "T", "user_id": "alice"},
        )
    assert resp.status_code == 409
    assert "not yet approved" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_create_agent_template_not_found_returns_502(
    client: httpx.AsyncClient,
) -> None:
    """Agent's 404 surfaces as 502 (TemplateRenderFailed) — we depended on
    an upstream and that upstream gave us nothing usable."""
    with respx.mock:
        respx.get(f"{HUB_URL}/api/admin/devices").mock(
            return_value=httpx.Response(200, json={"devices": [_device_payload("d1")]})
        )
        respx.post(f"{AGENT_URL}/api/admin/personas/templates/missing/render").mock(
            return_value=httpx.Response(404, json={"detail": "not found"})
        )
        resp = await client.post(
            "/api/devices/d1/agents",
            json={"template_id": "missing", "user_id": "alice"},
        )
    assert resp.status_code == 502


@pytest.mark.asyncio
async def test_create_agent_rejects_empty_user_id_via_pydantic(
    client: httpx.AsyncClient,
) -> None:
    resp = await client.post(
        "/api/devices/d1/agents",
        json={"template_id": "T", "user_id": ""},
    )
    assert resp.status_code == 422  # Pydantic min_length=1


@pytest.mark.asyncio
async def test_create_agent_missing_template_id_returns_422(
    client: httpx.AsyncClient,
) -> None:
    resp = await client.post(
        "/api/devices/d1/agents",
        json={"user_id": "alice"},
    )
    assert resp.status_code == 422


# ---- switch active --------------------------------------------------------


@pytest.mark.asyncio
async def test_switch_active_to_unknown_agent_returns_404(
    client: httpx.AsyncClient,
) -> None:
    """No mapping for the device → AgentNotFound surfaces as 404."""
    resp = await client.post(
        "/api/devices/nope/active-agent",
        json={"agent_id": "anything"},
    )
    assert resp.status_code == 404  # DeviceNotFound is also 404


# ---- delete agent ---------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_unknown_agent_returns_404(client: httpx.AsyncClient) -> None:
    resp = await client.delete("/api/devices/nope/agents/imaginary")
    assert resp.status_code == 404


# ---- soul endpoints -------------------------------------------------------


@pytest.mark.asyncio
async def test_update_soul_oversized_returns_413(client: httpx.AsyncClient) -> None:
    """Soul above the 256KB cap must be rejected with 413, not silently truncated.

    Seed a real bind via the happy path so we have a valid agent_id to
    aim at; then exceed the cap.
    """
    with respx.mock:
        respx.get(f"{HUB_URL}/api/admin/devices").mock(
            return_value=httpx.Response(200, json={"devices": [_device_payload("d1")]})
        )
        respx.post(f"{AGENT_URL}/api/admin/personas/templates/T/render").mock(
            return_value=httpx.Response(200, json={
                "markdown": "# soul\n",
                "template_id": "T",
                "template_revision": 1,
            })
        )
        create = await client.post(
            "/api/devices/d1/agents",
            json={"template_id": "T", "user_id": "alice"},
        )
        agent_id = create.json()["agent_id"]

        resp = await client.put(
            f"/api/devices/d1/agents/{agent_id}/soul",
            json={"markdown": "x" * (MAX_SOUL_SIZE_BYTES + 1)},
        )
    assert resp.status_code == 413


@pytest.mark.asyncio
async def test_get_soul_returns_markdown_and_size(client: httpx.AsyncClient) -> None:
    """Round-trip: create, then GET — markdown matches what we rendered."""
    with respx.mock:
        respx.get(f"{HUB_URL}/api/admin/devices").mock(
            return_value=httpx.Response(200, json={"devices": [_device_payload("d1")]})
        )
        respx.post(f"{AGENT_URL}/api/admin/personas/templates/T/render").mock(
            return_value=httpx.Response(200, json={
                "markdown": "# Hello soul\n",
                "template_id": "T",
                "template_revision": 1,
            })
        )
        create = await client.post(
            "/api/devices/d1/agents",
            json={"template_id": "T", "user_id": "alice"},
        )
        agent_id = create.json()["agent_id"]
        resp = await client.get(f"/api/devices/d1/agents/{agent_id}/soul")
    assert resp.status_code == 200
    body = resp.json()
    assert body["markdown"] == "# Hello soul\n"
    assert body["size_bytes"] == len(b"# Hello soul\n")
