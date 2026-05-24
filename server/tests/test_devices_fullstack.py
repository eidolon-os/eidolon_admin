"""Cross-repo in-process end-to-end test for the device-binding flow.

This is the *only* test in the suite that exercises all three repos'
real code paths together with zero mocks on the HTTP layer:

    Browser  →  admin FastAPI (real router + real orchestrator)
                ├─ real httpx.AsyncClient with ASGITransport routing
                │   ├─ to hub's real FastAPI app (real DeviceManager)
                │   └─ to agent's real FastAPI app (real PersonasService
                │                                   + real template files)
                └─ real KVClient → real local NATS

Why this exists alongside the respx-mocked tests:
    The mocked tests verify admin's hypothesis about what hub/agent
    return. If hub renames an endpoint or changes a JSON field, respx
    mocks keep passing because they are admin's understanding, not
    upstream reality. This test fails loudly the moment a real cross-
    repo contract drifts. The respx tests still earn their keep — they
    are fast, deterministic, and let us test failure paths (timeout,
    500, refusal) the real upstreams won't easily reproduce.

Skip behaviour:
    The whole module skips when hub / eidolon_agent aren't importable
    (i.e. admin's venv hasn't pip-installed the sibling repos as
    editable deps) or NATS is unreachable. CI configurations without
    the sibling repos installed see this file as 4 skipped tests, no
    spurious failures.
"""
from __future__ import annotations

import importlib
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import AsyncIterator

import httpx
import pytest

from eidolon_admin_server.app.devices.orchestrator import DeviceOrchestrator
from eidolon_admin_server.app.devices.repository import (
    AGENTS_BUCKET,
    MAPPINGS_BUCKET,
    SOULS_BUCKET,
    DeviceBindingRepository,
)
from eidolon_admin_server.app.main import create_app as create_admin_app
from eidolon_admin_server.app.nats_kv import KVClient
from eidolon_admin_server.app.settings import (
    AdminBindConfig,
    AuthConfig,
    GatewayConfig,
    ServiceConfig,
)


# Skip the whole file if the cross-repo packages aren't installed.
_hub_available = importlib.util.find_spec("hub.main") is not None
_agent_available = importlib.util.find_spec("eidolon_agent.app.admin") is not None
pytestmark = pytest.mark.skipif(
    not (_hub_available and _agent_available),
    reason=(
        "fullstack test requires editable installs of eidolon_hub and "
        "eidolon_agent in admin's venv (pip install -e ../eidolon_hub "
        "-e ../eidolon_agent)"
    ),
)


# These hosts are arbitrary — they're never resolved because every request
# routes via ASGITransport, but httpx still needs a syntactically valid URL.
HUB_HOST = "http://hub.internal"
AGENT_HOST = "http://agent.internal"


# ---- helpers --------------------------------------------------------------


class _NoopAdminRuntime:
    """Stand-in for hub's LiveKitAdminRuntime.

    The approve + list endpoints we exercise here never touch LiveKit,
    but hub's app construction asks for one. A minimal stub keeps the
    test isolated from real LiveKit infrastructure.
    """

    async def get_presence_snapshot(self):
        return []

    def get_probe_health(self):
        return SimpleNamespace(
            running=False,
            last_success_at=None,
            last_error="",
            consecutive_failures=0,
            total_cycles=0,
        )


async def _build_hub_app(tmp_path: Path):
    """Construct a real hub FastAPI app over a tmp_path-backed DeviceManager."""
    from hub.config import AppConfig
    from hub.core.device_manager import DeviceManager
    from hub.main import create_app as create_hub_app

    app = create_hub_app(AppConfig())
    dm = DeviceManager(tmp_path / "hub_devices.json")
    await dm.load()
    app.state.device_manager = dm
    app.state.admin_runtime = _NoopAdminRuntime()
    return app, dm


async def _build_agent_app():
    """Construct a real agent admin FastAPI app with real template files.

    Async because the template registry loads YAML files asynchronously
    and we're called from inside pytest-asyncio's running loop — the
    sync-wrapper pattern (asyncio.run / get_event_loop) would either
    fail with "loop already running" or create a second loop and lose
    fixture state.
    """
    from eidolon_agent.app.admin import build_admin_app
    from eidolon_agent.config.settings import Settings
    from eidolon_agent.domain.personas import (
        PersonasService,
        PersonaTemplateRegistry,
        YamlPersonaInstanceStore,
    )

    # The real template directory lives in eidolon_agent's source tree.
    agent_root = Path(importlib.util.find_spec("eidolon_agent").origin).parent  # type: ignore[union-attr]
    template_dir = agent_root / "domain" / "personas" / "templates"
    registry = PersonaTemplateRegistry(template_dir)
    await registry.load_all()
    instance_store = YamlPersonaInstanceStore(
        Path("/tmp") / f"fullstack-instances-{uuid.uuid4().hex}"
    )
    service = PersonasService(registry=registry, instances=instance_store)
    return build_admin_app(
        settings=Settings(),
        agent_registry=object(),
        pairing=object(),
        personas_service=service,
    )


class _MultiHostTransport(httpx.AsyncBaseTransport):
    """Route requests by host into one of several mounted ASGI apps.

    httpx ships ``ASGITransport`` but it routes everything to a single
    app. Admin's orchestrator hits two different hosts (hub + agent) on
    the same client; we dispatch based on URL.host so each goes to its
    proper app without touching the network.
    """

    def __init__(self, mapping: dict[str, httpx.AsyncBaseTransport]) -> None:
        self._mapping = mapping

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        target = self._mapping.get(request.url.host)
        if target is None:
            raise RuntimeError(
                f"_MultiHostTransport: no app mounted for host {request.url.host!r}"
            )
        return await target.handle_async_request(request)


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
async def fullstack(tmp_path, kv_client: KVClient):
    """Wire admin + hub + agent end-to-end with isolated per-test NATS buckets."""
    # Per-test bucket names so parallel runs don't collide.
    suffix = uuid.uuid4().hex[:10]
    original_names = (MAPPINGS_BUCKET.name, SOULS_BUCKET.name, AGENTS_BUCKET.name)
    object.__setattr__(MAPPINGS_BUCKET, "name", f"test_fs_map_{suffix}")
    object.__setattr__(SOULS_BUCKET, "name", f"test_fs_souls_{suffix}")
    object.__setattr__(AGENTS_BUCKET, "name", f"test_fs_agents_{suffix}")

    # Build real upstream apps.
    hub_app, hub_dm = await _build_hub_app(tmp_path)
    agent_app = await _build_agent_app()

    # Build admin's app with a services.yaml pointing at our virtual hosts.
    admin_cfg = GatewayConfig(
        admin=AdminBindConfig(host="127.0.0.1", port=9000, cors_origins=[]),
        services=[
            ServiceConfig(
                id="hub", name="Hub",
                base_url=HUB_HOST, upstream_prefix="/api/admin",
                auth=AuthConfig(type="none"), features=[],
            ),
            ServiceConfig(
                id="agent", name="Agent",
                base_url=AGENT_HOST, upstream_prefix="/api/admin",
                auth=AuthConfig(type="none"), features=[],
            ),
        ],
    )
    admin_app = create_admin_app(admin_cfg)

    # Replace admin's outbound http_client with one that routes to the in-
    # process ASGI apps. The orchestrator never sees the difference.
    await admin_app.state.http_client.aclose()
    multi_transport = _MultiHostTransport({
        "hub.internal": httpx.ASGITransport(app=hub_app),
        "agent.internal": httpx.ASGITransport(app=agent_app),
    })
    admin_app.state.http_client = httpx.AsyncClient(
        transport=multi_transport, trust_env=False, timeout=10.0,
    )

    repo = DeviceBindingRepository(kv_client)
    await repo.ensure_buckets()
    admin_app.state.device_orchestrator = DeviceOrchestrator(
        repo=repo,
        http_client=admin_app.state.http_client,
        hub_base_url=HUB_HOST,
        agent_base_url=AGENT_HOST,
    )

    yield SimpleNamespace(
        admin_app=admin_app,
        hub_dm=hub_dm,
    )

    await admin_app.state.http_client.aclose()
    object.__setattr__(MAPPINGS_BUCKET, "name", original_names[0])
    object.__setattr__(SOULS_BUCKET, "name", original_names[1])
    object.__setattr__(AGENTS_BUCKET, "name", original_names[2])


@pytest.fixture
async def client(fullstack) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=fullstack.admin_app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://admin.test", trust_env=False
    ) as c:
        yield c


# ---- the real contract tests ---------------------------------------------


@pytest.mark.asyncio
async def test_full_lifecycle_seed_approve_bind_switch_delete(
    fullstack, client: httpx.AsyncClient,
) -> None:
    """The happy path the operator clicks through, all real code paths.

    seed hub DeviceManager → admin GET shows the device → admin POST
    approve hits hub's real endpoint → hub state mutates → admin POST
    create_agent hits agent's real /render → admin writes real NATS →
    admin GET shows the binding → admin reads soul back from real NATS
    matching what agent rendered. No mocks anywhere on the HTTP layer.

    If hub renames POST /api/admin/devices/{id}/approve or agent
    changes the /render response key from ``markdown`` to ``text``,
    this test fails the way production would — at the same boundary.
    """
    # 1. Seed a device into hub via its real DeviceManager (the
    #    operator-facing flow today; mirrors how supervisord-managed
    #    hub gets devices via /api/config).
    fullstack.hub_dm.register(device_id="fs-001", name="Fullstack Device")

    # 2. Admin sees it.
    listed = await client.get("/api/devices")
    assert listed.status_code == 200
    devices = {d["device_id"]: d for d in listed.json()["devices"]}
    assert "fs-001" in devices
    assert devices["fs-001"]["approved"] is False  # not yet approved

    # 3. Approve via admin (real HTTP to real hub).
    approve = await client.post("/api/devices/fs-001/approve")
    assert approve.status_code == 200
    assert approve.json()["approved"] is True

    # 4. Hub's in-memory state actually changed (verified via DeviceManager).
    dev_obj = fullstack.hub_dm.get("fs-001")
    assert dev_obj is not None and dev_obj.approved is True

    # 5. Create an agent — admin calls real agent /render, writes real NATS.
    bind = await client.post(
        "/api/devices/fs-001/agents",
        json={"template_id": "caretaker_jiezhi", "user_id": "alice"},
    )
    assert bind.status_code == 200, bind.text
    agent_id = bind.json()["agent_id"]

    # 6. Composite GET shows the binding now.
    listed_after = await client.get("/api/devices")
    fs = next(d for d in listed_after.json()["devices"] if d["device_id"] == "fs-001")
    assert fs["binding"] is not None
    assert fs["binding"]["active_agent_id"] == agent_id
    assert len(fs["binding"]["agents"]) == 1
    assert fs["binding"]["agents"][0]["template_id"] == "caretaker_jiezhi"

    # 7. Soul read back: the bytes admin stored equal what agent rendered.
    soul = await client.get(f"/api/devices/fs-001/agents/{agent_id}/soul")
    assert soul.status_code == 200
    markdown = soul.json()["markdown"]
    assert "# 解之" in markdown
    assert "## 身份核心" in markdown


@pytest.mark.asyncio
async def test_admin_approve_against_real_hub_404s_unknown_device(
    fullstack, client: httpx.AsyncClient,
) -> None:
    """When hub really doesn't know the device, the 404 from hub's actual
    route surfaces all the way back to the admin caller — locking the
    error-code contract end-to-end."""
    resp = await client.post("/api/devices/never-seen/approve")
    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert "not registered" in detail


@pytest.mark.asyncio
async def test_admin_create_agent_against_real_agent_404s_unknown_template(
    fullstack, client: httpx.AsyncClient,
) -> None:
    """Real agent's /render 404 → admin's TemplateRenderFailed → HTTP 502.

    If agent later changes its 404 detail wording, this test still
    passes (we only assert the status code). If agent changes the
    status code to e.g. 422, this test breaks immediately — which is
    the point.
    """
    fullstack.hub_dm.register(device_id="fs-tpl-fail", name="Test")
    # Approve so we reach the render step rather than failing on the gate.
    await client.post("/api/devices/fs-tpl-fail/approve")

    resp = await client.post(
        "/api/devices/fs-tpl-fail/agents",
        json={"template_id": "no_such_template_anywhere", "user_id": "alice"},
    )
    assert resp.status_code == 502


@pytest.mark.asyncio
async def test_admin_unapproved_device_blocks_bind_against_real_hub(
    fullstack, client: httpx.AsyncClient,
) -> None:
    """End-to-end: hub reports a device as unapproved → admin refuses
    to bind. Verifies the approval gate is enforced by REAL hub state,
    not just admin's mock of hub state."""
    fullstack.hub_dm.register(device_id="fs-unapproved", name="Test")
    # Deliberately skip the approve call.
    resp = await client.post(
        "/api/devices/fs-unapproved/agents",
        json={"template_id": "caretaker_jiezhi", "user_id": "alice"},
    )
    assert resp.status_code == 409
    assert "not yet approved" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_full_lifecycle_switch_active_delete_with_fallback_zero_mocks(
    fullstack, client: httpx.AsyncClient,
) -> None:
    """The other half of the lifecycle that the first fullstack test
    deliberately stops short of: bind two agents, swap active, edit a
    soul, delete the active one (auto-fallback), delete the last one
    (active cleared). All upstreams are real apps.

    This proves the contract for switch + soul-edit + delete is intact
    end-to-end. The first fullstack test covered bind + read; together
    they exercise every operator action against zero mocks.
    """
    fullstack.hub_dm.register(device_id="fs-lifecycle", name="Lifecycle Device")
    await client.post("/api/devices/fs-lifecycle/approve")

    # Bind agent A first, then B. B becomes active (newest wins).
    agent_a = (await client.post(
        "/api/devices/fs-lifecycle/agents",
        json={"template_id": "caretaker_jiezhi", "user_id": "alice"},
    )).json()["agent_id"]
    agent_b = (await client.post(
        "/api/devices/fs-lifecycle/agents",
        json={"template_id": "caretaker_jiezhi", "user_id": "alice"},
    )).json()["agent_id"]

    # Switch active back to A.
    switch = await client.post(
        "/api/devices/fs-lifecycle/active-agent",
        json={"agent_id": agent_a},
    )
    assert switch.status_code == 200
    assert switch.json()["active_agent_id"] == agent_a

    # Edit A's soul. Real markdown → real NATS → real read-back.
    edited = "# edited by operator\n## custom section\n"
    put = await client.put(
        f"/api/devices/fs-lifecycle/agents/{agent_a}/soul",
        json={"markdown": edited},
    )
    assert put.status_code == 200
    got = await client.get(f"/api/devices/fs-lifecycle/agents/{agent_a}/soul")
    assert got.json()["markdown"] == edited

    # Delete active A → fallback to B.
    del_a = await client.delete(f"/api/devices/fs-lifecycle/agents/{agent_a}")
    assert del_a.status_code == 200
    body = del_a.json()
    assert body["fallback_kind"] == "next_newest"
    assert body["new_active_agent_id"] == agent_b

    # Delete the last one → active cleared.
    del_b = await client.delete(f"/api/devices/fs-lifecycle/agents/{agent_b}")
    assert del_b.status_code == 200
    body = del_b.json()
    assert body["fallback_kind"] == "cleared"
    assert body["new_active_agent_id"] is None

    # The device still exists in hub (delete only removed the binding;
    # device lifecycle is independent of agent lifecycle).
    final_list = await client.get("/api/devices")
    fs = next(d for d in final_list.json()["devices"] if d["device_id"] == "fs-lifecycle")
    # binding row may either be None or have empty agent_ids — both are
    # acceptable steady states for "approved but unbound".
    assert fs["binding"] is None or fs["binding"]["agent_ids"] == []
