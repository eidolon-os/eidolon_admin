"""Tests for /api/resolve/* aggregator (Phase 29.G).

Verifies the cross-entity join: device→binding→agent→user→memory_url
and user→active_agent→agent→user→memory_url. The whole point of
resolve is "channel asks once, admin joins" — so the tests are
structured to pin what channel sees.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import AsyncIterator

import httpx
import pytest
import respx
from fastapi import FastAPI

from eidolon_admin_server.app.nats_kv import KVClient
from eidolon_admin_server.app.registry import buckets as buckets_module
from eidolon_admin_server.app.registry.agents.repository import (
    AgentMetadata,
    AgentMetadataRepository,
)
from eidolon_admin_server.app.registry.devices.repository import (
    DeviceBindingRepository,
)
from eidolon_admin_server.app.registry.resolve import (
    ResolveDeviceNotBound,
    ResolveOrchestrator,
    ResolveUserNoActiveAgent,
    router as resolve_router,
)
from eidolon_admin_server.app.registry.resolve.orchestrator import (
    ResolveError404,
    ResolveUpstreamDown,
)
from eidolon_admin_server.app.registry.schemas.device import DeviceBinding
from eidolon_admin_server.app.registry.schemas.tenant import CreateTenantRequest
from eidolon_admin_server.app.registry.templates import (
    TemplateAgentClient,
    TemplateOrchestrator,
)
from eidolon_admin_server.app.registry.tenants import (
    TenantOrchestrator,
    TenantRepository,
)
from eidolon_admin_server.app.registry.users import (
    MemoryUserClient,
    UserMetadataRepository,
    UserOrchestrator,
)
from eidolon_admin_server.app.registry.users.repository import UserMetadata


MEMORY_URL = "http://memory.test"
AGENT_URL = "http://agent.test"


def _memory_user(user_id: str = "alice") -> dict:
    return {
        "spec": {
            "user_id": user_id, "tenant_id": "default", "display_name": user_id,
            "palace_path": "",
            "consolidator": {
                "enabled": True, "interval_hours": 6.0, "window_days": 30,
                "min_drawers": 3, "min_confidence": 0.6,
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
        "health": {
            "worker_running": True, "mcp_reachable": True,
            "palace_initialized": True, "note": "",
        },
        "active_agent_id": None, "agent_ids": [],
    }


_TEMPLATE_YAML = (
    "metadata:\n  template_id: caretaker_jiezhi\n  name: J\n  archetype: c"
)


# ---- fixtures ---------------------------------------------------------------


@pytest.fixture
async def kv_client() -> AsyncIterator[KVClient]:
    client = KVClient()
    try:
        await client.connect()
    except Exception:
        pytest.skip("NATS not reachable at 127.0.0.1:4222")
    yield client
    await client.close()


@pytest.fixture
async def buckets_setup(kv_client: KVClient) -> AsyncIterator[None]:
    suffix = uuid.uuid4().hex[:10]
    orig = {
        "t": buckets_module.TENANTS_BUCKET.name,
        "u": buckets_module.USERS_METADATA_BUCKET.name,
        "a": buckets_module.AGENTS_METADATA_BUCKET.name,
        "d": buckets_module.DEVICE_BINDINGS_BUCKET.name,
    }
    object.__setattr__(buckets_module.TENANTS_BUCKET, "name", f"test_t_{suffix}")
    object.__setattr__(buckets_module.USERS_METADATA_BUCKET, "name", f"test_u_{suffix}")
    object.__setattr__(buckets_module.AGENTS_METADATA_BUCKET, "name", f"test_a_{suffix}")
    object.__setattr__(buckets_module.DEVICE_BINDINGS_BUCKET, "name", f"test_d_{suffix}")
    for b in (
        buckets_module.TENANTS_BUCKET, buckets_module.USERS_METADATA_BUCKET,
        buckets_module.AGENTS_METADATA_BUCKET, buckets_module.DEVICE_BINDINGS_BUCKET,
    ):
        await kv_client.ensure_bucket(b)
    yield
    object.__setattr__(buckets_module.TENANTS_BUCKET, "name", orig["t"])
    object.__setattr__(buckets_module.USERS_METADATA_BUCKET, "name", orig["u"])
    object.__setattr__(buckets_module.AGENTS_METADATA_BUCKET, "name", orig["a"])
    object.__setattr__(buckets_module.DEVICE_BINDINGS_BUCKET, "name", orig["d"])


@pytest.fixture
async def http_client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as c:
        yield c


@pytest.fixture
async def orchestrator(
    kv_client: KVClient,
    buckets_setup: None,
    http_client: httpx.AsyncClient,
) -> AsyncIterator[ResolveOrchestrator]:
    # Tenants → Users → Agents → Templates → Devices wiring (subset).
    tenant_orch = TenantOrchestrator(TenantRepository(kv_client))
    await tenant_orch.create(
        CreateTenantRequest(tenant_id="default", display_name="Default")
    )
    user_orch = UserOrchestrator(
        memory_client=MemoryUserClient(http_client, MEMORY_URL),
        metadata_repo=UserMetadataRepository(kv_client),
        tenant_orchestrator=tenant_orch,
    )
    template_orch = TemplateOrchestrator(
        TemplateAgentClient(http_client, AGENT_URL)
    )
    agent_meta_repo = AgentMetadataRepository(kv_client)
    binding_repo = DeviceBindingRepository(kv_client)

    yield ResolveOrchestrator(
        binding_repo=binding_repo,
        agent_meta_repo=agent_meta_repo,
        user_orchestrator=user_orch,
        template_orchestrator=template_orch,
    )


# ---- resolve_device --------------------------------------------------------


async def test_resolve_device_unbound_returns_412(
    orchestrator: ResolveOrchestrator,
) -> None:
    with pytest.raises(ResolveDeviceNotBound) as exc_info:
        await orchestrator.resolve_device("esp-not-bound")
    assert exc_info.value.status_code == 412


async def test_resolve_device_drift_agent_gone_returns_404(
    orchestrator: ResolveOrchestrator,
) -> None:
    """Device has a binding but the agent_id it points at is gone from
    admin's registry. We return 404 with a precise diagnostic."""
    await orchestrator._bindings.put(
        "esp-drift",
        DeviceBinding(agent_id="ag-deleted", bound_at=datetime.now(timezone.utc)),
    )
    with pytest.raises(ResolveError404, match="ag-deleted") as exc_info:
        await orchestrator.resolve_device("esp-drift")
    assert exc_info.value.status_code == 404


async def test_resolve_device_happy_path(
    orchestrator: ResolveOrchestrator,
) -> None:
    """All the pieces are in place — channel gets one envelope."""
    # Set up KV: binding → agent → user metadata
    base = datetime.now(timezone.utc).isoformat()
    await orchestrator._bindings.put(
        "esp-1",
        DeviceBinding(agent_id="ag-1", bound_at=datetime.now(timezone.utc)),
    )
    await orchestrator._agents.put(
        "ag-1",
        AgentMetadata(
            tenant_id="default", user_id="alice",
            template_id="caretaker_jiezhi", template_revision=1,
            display_name="Caretaker for Alice", created_at=base,
        ),
    )
    await orchestrator._users._meta.put(  # type: ignore[attr-defined]
        "alice", UserMetadata(tenant_id="default", display_name="Alice")
    )

    with respx.mock() as rsx:
        rsx.get(f"{MEMORY_URL}/api/admin/users/alice").mock(
            return_value=httpx.Response(200, json=_memory_user("alice"))
        )
        rsx.get(
            f"{AGENT_URL}/api/admin/personas/templates/caretaker_jiezhi/raw"
        ).mock(return_value=httpx.Response(200, text=_TEMPLATE_YAML))
        ctx = await orchestrator.resolve_device("esp-1")
    assert ctx.tenant_id == "default"
    assert ctx.user_id == "alice"
    assert ctx.agent_id == "ag-1"
    assert ctx.template_id == "caretaker_jiezhi"
    assert ctx.device_id == "esp-1"
    assert ctx.memory_mcp_url.endswith("/mcp")
    assert ctx.soul_preview.startswith("metadata:")


# ---- resolve_user ---------------------------------------------------------


async def test_resolve_user_no_metadata_returns_404(
    orchestrator: ResolveOrchestrator,
) -> None:
    with pytest.raises(ResolveError404) as exc_info:
        await orchestrator.resolve_user("unknown")
    assert exc_info.value.status_code == 404


async def test_resolve_user_no_active_agent_returns_412(
    orchestrator: ResolveOrchestrator,
) -> None:
    """User exists in admin metadata but no active_agent_id set."""
    await orchestrator._users._meta.put(  # type: ignore[attr-defined]
        "alice", UserMetadata(tenant_id="default", active_agent_id=None)
    )
    with pytest.raises(ResolveUserNoActiveAgent) as exc_info:
        await orchestrator.resolve_user("alice")
    assert exc_info.value.status_code == 412


async def test_resolve_user_happy_path(
    orchestrator: ResolveOrchestrator,
) -> None:
    base = datetime.now(timezone.utc).isoformat()
    await orchestrator._users._meta.put(  # type: ignore[attr-defined]
        "alice", UserMetadata(tenant_id="default", active_agent_id="ag-1")
    )
    await orchestrator._agents.put(
        "ag-1",
        AgentMetadata(
            tenant_id="default", user_id="alice",
            template_id="caretaker_jiezhi", template_revision=1,
            display_name="A", created_at=base,
        ),
    )
    with respx.mock() as rsx:
        rsx.get(f"{MEMORY_URL}/api/admin/users/alice").mock(
            return_value=httpx.Response(200, json=_memory_user("alice"))
        )
        rsx.get(
            f"{AGENT_URL}/api/admin/personas/templates/caretaker_jiezhi/raw"
        ).mock(return_value=httpx.Response(200, text=_TEMPLATE_YAML))
        ctx = await orchestrator.resolve_user("alice")
    assert ctx.user_id == "alice"
    assert ctx.agent_id == "ag-1"
    assert ctx.device_id is None  # user-path doesn't carry a device


# ---- router HTTP ---------------------------------------------------------


@pytest.fixture
async def client(
    orchestrator: ResolveOrchestrator,
) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.state.resolve_orchestrator = orchestrator
    app.include_router(resolve_router, prefix="/api")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", trust_env=False
    ) as c:
        yield c


async def test_http_resolve_device_412_when_unbound(
    client: httpx.AsyncClient,
) -> None:
    r = await client.get("/api/resolve/device/ghost")
    assert r.status_code == 412
    assert "not bound" in r.json()["detail"]


async def test_http_resolve_user_404_when_unregistered(
    client: httpx.AsyncClient,
) -> None:
    r = await client.get("/api/resolve/user/unknown")
    assert r.status_code == 404


async def test_http_503_when_orchestrator_missing() -> None:
    app = FastAPI()
    app.state.resolve_orchestrator = None
    app.include_router(resolve_router, prefix="/api")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/api/resolve/device/x")
    assert r.status_code == 503
