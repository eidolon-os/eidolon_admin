"""Tests for /api/resolve/* aggregator (Phase 29.G).

Verifies the cross-entity join: device→binding→agent→user→memory_url
and user→active_agent→agent→user→memory_url. The whole point of
resolve is "channel asks once, admin joins" — so the tests are
structured to pin what channel sees.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from io import BytesIO
from typing import AsyncIterator
import wave

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
    HubDeviceClient,
)
from eidolon_admin_server.app.registry.resolve import (
    ResolveDeviceNotBound,
    ResolveDeviceUnavailable,
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
from eidolon_admin_server.app.registry.voiceprints import VoiceprintStore


MEMORY_URL = "http://memory.test"
AGENT_URL = "http://agent.test"
HUB_URL = "http://hub.test"


def _hub_device(device_id: str, *, enabled: bool = True, approved: bool = True) -> dict:
    return {
        "device_id": device_id,
        "name": device_id,
        "kind": "esp32",
        "enabled": enabled,
        "approved": approved,
        "approved_at": datetime.now(timezone.utc).isoformat() if approved else None,
        "last_seen": datetime.now(timezone.utc).isoformat(),
        "status": "online",
    }


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
        # 29.K: memory now exposes the per-user MCP URL on its view
        # envelope, so resolve no longer synthesizes from convention.
        "mcp_http_url": "http://127.0.0.1:8030/mcp",
    }


_TEMPLATE_YAML = (
    "metadata:\n  template_id: caretaker_jiezhi\n  name: J\n  archetype: c"
)


def _wav_bytes() -> bytes:
    buf = BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00\x00" * 1600)
    return buf.getvalue()


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
    tmp_path,
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
        hub_client=HubDeviceClient(http_client, HUB_URL),
        agent_meta_repo=agent_meta_repo,
        user_orchestrator=user_orch,
        template_orchestrator=template_orch,
        voiceprint_store=VoiceprintStore(tmp_path),
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
    with respx.mock() as rsx:
        rsx.get(f"{HUB_URL}/api/admin/devices/esp-drift").mock(
            return_value=httpx.Response(200, json=_hub_device("esp-drift"))
        )
        with pytest.raises(ResolveError404, match="ag-deleted") as exc_info:
            await orchestrator.resolve_device("esp-drift")
    assert exc_info.value.status_code == 404


async def test_resolve_device_disabled_returns_412(
    orchestrator: ResolveOrchestrator,
) -> None:
    await orchestrator._bindings.put(
        "esp-disabled",
        DeviceBinding(agent_id="ag-1", bound_at=datetime.now(timezone.utc)),
    )
    with respx.mock() as rsx:
        rsx.get(f"{HUB_URL}/api/admin/devices/esp-disabled").mock(
            return_value=httpx.Response(
                200, json=_hub_device("esp-disabled", enabled=False),
            )
        )
        with pytest.raises(ResolveDeviceUnavailable) as exc_info:
            await orchestrator.resolve_device("esp-disabled")
    assert exc_info.value.status_code == 412


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
        rsx.get(f"{HUB_URL}/api/admin/devices/esp-1").mock(
            return_value=httpx.Response(200, json=_hub_device("esp-1"))
        )
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
    # 29.K: memory_mcp_url comes from memory's user view, not synth.
    # The test fixture pins the value memory would return.
    assert ctx.memory_mcp_url == "http://127.0.0.1:8030/mcp"
    assert ctx.soul_preview.startswith("metadata:")
    assert ctx.voiceprint.enabled is False


async def test_resolve_device_includes_voiceprint_summary(
    orchestrator: ResolveOrchestrator,
) -> None:
    base = datetime.now(timezone.utc).isoformat()
    await orchestrator._bindings.put(
        "esp-voice",
        DeviceBinding(agent_id="ag-voice", bound_at=datetime.now(timezone.utc)),
    )
    await orchestrator._agents.put(
        "ag-voice",
        AgentMetadata(
            tenant_id="default", user_id="alice",
            template_id="caretaker_jiezhi", template_revision=1,
            display_name="Caretaker for Alice", created_at=base,
        ),
    )
    await orchestrator._users._meta.put(  # type: ignore[attr-defined]
        "alice", UserMetadata(tenant_id="default", display_name="Alice")
    )

    store = orchestrator._voiceprints  # type: ignore[attr-defined]
    enrollment = store.create_enrollment(
        tenant_id="default",
        user_id="alice",
        provider="noop",
        model="noop",
        sample_rate=16000,
    )
    store.add_sample(
        enrollment_id=enrollment.enrollment_id,
        tenant_id="default",
        user_id="alice",
        wav_bytes=_wav_bytes(),
    )
    profile = store.complete_enrollment(
        enrollment_id=enrollment.enrollment_id,
        tenant_id="default",
        user_id="alice",
    )

    with respx.mock() as rsx:
        rsx.get(f"{HUB_URL}/api/admin/devices/esp-voice").mock(
            return_value=httpx.Response(200, json=_hub_device("esp-voice"))
        )
        rsx.get(f"{MEMORY_URL}/api/admin/users/alice").mock(
            return_value=httpx.Response(200, json=_memory_user("alice"))
        )
        rsx.get(
            f"{AGENT_URL}/api/admin/personas/templates/caretaker_jiezhi/raw"
        ).mock(return_value=httpx.Response(200, text=_TEMPLATE_YAML))
        ctx = await orchestrator.resolve_device("esp-voice")

    assert ctx.voiceprint.enabled is True
    assert ctx.voiceprint.profile_id == profile.profile_id
    assert ctx.voiceprint.provider == "noop"


async def test_resolve_propagates_empty_mcp_url_when_memory_omits_it(
    orchestrator: ResolveOrchestrator,
) -> None:
    """If memory's user view doesn't carry ``mcp_http_url`` (e.g.
    pre-29.K memory build), resolve propagates the empty string rather
    than fabricating one. Channel will then refuse to dial — preferable
    to silently pointing at a stale port. Pinned because the old code
    synthesized; we don't want that to creep back."""
    base_iso = datetime(2025, 1, 1, tzinfo=timezone.utc).isoformat()
    base_dt = datetime(2025, 1, 1, tzinfo=timezone.utc)
    await orchestrator._agents.put(  # type: ignore[attr-defined]
        "ag-stale", AgentMetadata(
            tenant_id="default", user_id="bob",
            template_id="caretaker_jiezhi", template_revision=1,
            display_name="for Bob", created_at=base_iso,
        ),
    )
    await orchestrator._bindings.put(  # type: ignore[attr-defined]
        "esp-stale", DeviceBinding(agent_id="ag-stale", bound_at=base_dt),
    )
    await orchestrator._users._meta.put(  # type: ignore[attr-defined]
        "bob", UserMetadata(tenant_id="default", display_name="Bob")
    )
    bare = _memory_user("bob")
    bare.pop("mcp_http_url")  # simulate old memory
    with respx.mock() as rsx:
        rsx.get(f"{HUB_URL}/api/admin/devices/esp-stale").mock(
            return_value=httpx.Response(200, json=_hub_device("esp-stale"))
        )
        rsx.get(f"{MEMORY_URL}/api/admin/users/bob").mock(
            return_value=httpx.Response(200, json=bare)
        )
        rsx.get(
            f"{AGENT_URL}/api/admin/personas/templates/caretaker_jiezhi/raw"
        ).mock(return_value=httpx.Response(200, text=_TEMPLATE_YAML))
        ctx = await orchestrator.resolve_device("esp-stale")
    assert ctx.memory_mcp_url == ""


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
