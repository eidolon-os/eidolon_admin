"""Tests for the new Devices module (Phase 29.G).

Replaces the obsolete test_devices_* files for the Phase 25 model
(device-creates-agent). The new tests pin the new contract:

  - device is bound to an EXISTING agent (no implicit creation)
  - bind validates agent_id against admin's registry (not just trusts it)
  - approved-but-unbound is the natural transient state
  - delete cascade: unregister wipes both admin's binding + hub's record
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import AsyncIterator

import httpx
import pytest
import respx
from eidolon_data import DataSettings, DataStore
from eidolon_data.adapters.admin_registry import EidolonDataDeviceBindingRepository
from fastapi import FastAPI

from eidolon_admin_server.app.registry.agents.repository import AgentMetadata
from eidolon_admin_server.app.registry.devices import (
    DeviceBadRequest,
    DeviceBindingRepository,
    DeviceDisabled,
    DeviceHubDown,
    DeviceNotApproved,
    DeviceNotFound,
    DeviceOrchestrator,
    HubDeviceClient,
    router as devices_router,
)
from eidolon_admin_server.app.registry.schemas.device import BindDeviceRequest


HUB_URL = "http://hub.test"


def _hub_device_record(
    device_id: str,
    *,
    approved: bool = True,
    enabled: bool = True,
    name: str = "Device",
) -> dict:
    return {
        "device_id": device_id,
        "name": name,
        "kind": "esp32",
        "enabled": enabled,
        "approved": approved,
        "approved_at": (
            datetime.now(timezone.utc).isoformat() if approved else None
        ),
        "last_seen": datetime.now(timezone.utc).isoformat(),
        "status": "online",
    }


def _hub_command_response(device_id: str) -> dict:
    return {
        "command_id": str(uuid.uuid4()),
        "device_id": device_id,
        "topic": "eidolon.control",
        "op": "config.refresh",
        "status": "sent",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "payload": {"reason": "admin_state_changed"},
        "ttl_ms": 30000,
        "qos": "ack",
        "priority": "normal",
        "error": "",
    }


def _hub_discovery_response() -> dict:
    return {
        "service_type": "_eidolon-hub._tcp.local.",
        "service_name": "Eidolon Hub._eidolon-hub._tcp.local.",
        "hostname": "eidolon-hub",
        "port": 8082,
        "registered": True,
        "ip": "192.168.1.50",
        "config_url": "http://192.168.1.50:8082/api/config",
        "last_registered_at": "2026-06-23T00:00:00+00:00",
        "last_updated_at": "2026-06-23T00:00:00+00:00",
        "last_error": "",
    }


# ---- fixtures ---------------------------------------------------------------


@pytest.fixture
async def data_store(tmp_path) -> AsyncIterator[DataStore]:
    store = DataStore.open(DataSettings(sqlite_path=str(tmp_path / "eidolon.sqlite3")))
    yield store
    await store.close()


@pytest.fixture
async def http_client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as c:
        yield c


@pytest.fixture
async def orchestrator(
    data_store: DataStore,
    http_client: httpx.AsyncClient,
) -> AsyncIterator[DeviceOrchestrator]:
    hub_client = HubDeviceClient(http_client, HUB_URL)
    binding_repo = DeviceBindingRepository(
        EidolonDataDeviceBindingRepository(data_store)
    )

    # Stub agent_lookup. Returns AgentMetadata for known ids, None for others.
    known_agents: dict[str, AgentMetadata] = {
        "ag-1": AgentMetadata(
            tenant_id="default", user_id="alice", template_id="caretaker_jiezhi",
            template_revision=1, display_name="A1", created_at="",
        ),
    }

    async def _agent_lookup(agent_id: str):
        return known_agents.get(agent_id)

    yield DeviceOrchestrator(
        hub_client=hub_client,
        binding_repo=binding_repo,
        agent_lookup=_agent_lookup,
    )


# ---- list / get -----------------------------------------------------------


async def test_list_empty_no_devices(orchestrator: DeviceOrchestrator) -> None:
    with respx.mock(base_url=HUB_URL) as rsx:
        rsx.get("/api/admin/devices").mock(
            return_value=httpx.Response(200, json={"devices": []})
        )
        assert await orchestrator.list_devices() == []


async def test_list_resolves_bound_devices_with_agent_metadata(
    orchestrator: DeviceOrchestrator,
) -> None:
    """A device with a binding should surface resolved_user_id +
    resolved_template_id (from the agent_lookup)."""
    from eidolon_admin_server.app.registry.schemas.device import DeviceBinding

    await orchestrator._bindings.put(
        "esp32-foo",
        DeviceBinding(agent_id="ag-1", bound_at=datetime.now(timezone.utc)),
    )
    with respx.mock(base_url=HUB_URL) as rsx:
        rsx.get("/api/admin/devices").mock(
            return_value=httpx.Response(
                200,
                json={"devices": [_hub_device_record("esp32-foo")]},
            )
        )
        devices = await orchestrator.list_devices()
    assert len(devices) == 1
    d = devices[0]
    assert d.binding is not None
    assert d.binding.agent_id == "ag-1"
    assert d.resolved_user_id == "alice"
    assert d.resolved_template_id == "caretaker_jiezhi"


async def test_binding_repository_accepts_mac_address_device_id(
    orchestrator: DeviceOrchestrator,
) -> None:
    from eidolon_admin_server.app.registry.schemas.device import DeviceBinding

    device_id = "1c:db:d4:7a:ef:0c"
    binding = DeviceBinding(agent_id="ag-1", bound_at=datetime.now(timezone.utc))

    await orchestrator._bindings.put(device_id, binding)

    stored = await orchestrator._bindings.get(device_id)
    assert stored is not None
    assert stored.agent_id == "ag-1"

    all_bindings = await orchestrator._bindings.list_all()
    assert device_id in all_bindings
    assert all_bindings[device_id].agent_id == "ag-1"


async def test_list_unbound_devices_have_no_resolved_fields(
    orchestrator: DeviceOrchestrator,
) -> None:
    with respx.mock(base_url=HUB_URL) as rsx:
        rsx.get("/api/admin/devices").mock(
            return_value=httpx.Response(
                200, json={"devices": [_hub_device_record("esp32-bar")]},
            )
        )
        devices = await orchestrator.list_devices()
    assert devices[0].binding is None
    assert devices[0].resolved_user_id is None


async def test_list_hub_unreachable_raises_503(
    orchestrator: DeviceOrchestrator,
) -> None:
    with respx.mock(base_url=HUB_URL) as rsx:
        rsx.get("/api/admin/devices").mock(side_effect=httpx.ConnectError("down"))
        with pytest.raises(DeviceHubDown):
            await orchestrator.list_devices()


async def test_get_404_propagates(orchestrator: DeviceOrchestrator) -> None:
    with respx.mock(base_url=HUB_URL) as rsx:
        rsx.get("/api/admin/devices/ghost").mock(return_value=httpx.Response(404))
        with pytest.raises(DeviceNotFound):
            await orchestrator.get_device("ghost")


# ---- bind ------------------------------------------------------------------


async def test_bind_happy_path(orchestrator: DeviceOrchestrator) -> None:
    with respx.mock(base_url=HUB_URL) as rsx:
        rsx.get("/api/admin/devices/esp-1").mock(
            return_value=httpx.Response(200, json=_hub_device_record("esp-1"))
        )
        refresh = rsx.post("/api/admin/devices/esp-1/commands").mock(
            return_value=httpx.Response(200, json=_hub_command_response("esp-1"))
        )
        view = await orchestrator.bind_device(
            "esp-1", BindDeviceRequest(agent_id="ag-1")
        )
    assert view.binding is not None
    assert view.binding.agent_id == "ag-1"
    assert view.resolved_user_id == "alice"

    # Binding persisted
    stored = await orchestrator._bindings.get("esp-1")
    assert stored is not None
    assert stored.agent_id == "ag-1"
    assert stored.interaction_mode is None  # Phase 6: unset by default
    assert refresh.called


async def test_bind_persists_interaction_mode_override(
    orchestrator: DeviceOrchestrator,
) -> None:
    """Phase 6: the optional interaction_mode on the bind request is stored on
    the binding (and surfaced via the device view's embedded binding)."""
    with respx.mock(base_url=HUB_URL) as rsx:
        rsx.get("/api/admin/devices/esp-1").mock(
            return_value=httpx.Response(200, json=_hub_device_record("esp-1"))
        )
        rsx.post("/api/admin/devices/esp-1/commands").mock(
            return_value=httpx.Response(200, json=_hub_command_response("esp-1"))
        )
        view = await orchestrator.bind_device(
            "esp-1",
            BindDeviceRequest(agent_id="ag-1", interaction_mode="half_duplex"),
        )
    assert view.binding is not None
    assert view.binding.interaction_mode == "half_duplex"

    stored = await orchestrator._bindings.get("esp-1")
    assert stored is not None
    assert stored.interaction_mode == "half_duplex"


async def test_bind_unapproved_device_rejects(
    orchestrator: DeviceOrchestrator,
) -> None:
    """Cannot bind a device that hasn't been approved by the operator yet."""
    with respx.mock(base_url=HUB_URL) as rsx:
        rsx.get("/api/admin/devices/esp-new").mock(
            return_value=httpx.Response(
                200, json=_hub_device_record("esp-new", approved=False),
            )
        )
        with pytest.raises(DeviceNotApproved):
            await orchestrator.bind_device(
                "esp-new", BindDeviceRequest(agent_id="ag-1")
            )


async def test_bind_disabled_device_rejects(
    orchestrator: DeviceOrchestrator,
) -> None:
    """Disabled devices keep their record, but cannot be bound into runtime."""
    with respx.mock(base_url=HUB_URL) as rsx:
        rsx.get("/api/admin/devices/esp-off").mock(
            return_value=httpx.Response(
                200, json=_hub_device_record("esp-off", enabled=False),
            )
        )
        with pytest.raises(DeviceDisabled):
            await orchestrator.bind_device(
                "esp-off", BindDeviceRequest(agent_id="ag-1")
            )


async def test_bind_to_missing_agent_returns_bad_request(
    orchestrator: DeviceOrchestrator,
) -> None:
    """agent_id must exist in admin's registry — otherwise we'd be storing
    a pointer to nothing."""
    with respx.mock(base_url=HUB_URL) as rsx:
        rsx.get("/api/admin/devices/esp-1").mock(
            return_value=httpx.Response(200, json=_hub_device_record("esp-1"))
        )
        with pytest.raises(DeviceBadRequest, match="agent 'ag-nonexistent'") as exc_info:
            await orchestrator.bind_device(
                "esp-1", BindDeviceRequest(agent_id="ag-nonexistent")
            )
    assert exc_info.value.status_code == 400


async def test_bind_missing_device_returns_404(
    orchestrator: DeviceOrchestrator,
) -> None:
    with respx.mock(base_url=HUB_URL) as rsx:
        rsx.get("/api/admin/devices/ghost").mock(return_value=httpx.Response(404))
        with pytest.raises(DeviceNotFound):
            await orchestrator.bind_device(
                "ghost", BindDeviceRequest(agent_id="ag-1")
            )


# ---- unbind ---------------------------------------------------------------


async def test_unbind_clears_binding(orchestrator: DeviceOrchestrator) -> None:
    """Unbind clears the binding KV but leaves hub's device record alone."""
    from eidolon_admin_server.app.registry.schemas.device import DeviceBinding

    await orchestrator._bindings.put(
        "esp-1",
        DeviceBinding(agent_id="ag-1", bound_at=datetime.now(timezone.utc)),
    )
    with respx.mock(base_url=HUB_URL) as rsx:
        rsx.get("/api/admin/devices/esp-1").mock(
            return_value=httpx.Response(200, json=_hub_device_record("esp-1"))
        )
        refresh = rsx.post("/api/admin/devices/esp-1/commands").mock(
            return_value=httpx.Response(200, json=_hub_command_response("esp-1"))
        )
        view = await orchestrator.unbind_device("esp-1")
    assert view.binding is None
    assert await orchestrator._bindings.get("esp-1") is None
    assert refresh.called


async def test_unbind_idempotent_when_not_bound(
    orchestrator: DeviceOrchestrator,
) -> None:
    """No binding → unbind is a no-op + still returns the view."""
    with respx.mock(base_url=HUB_URL) as rsx:
        rsx.get("/api/admin/devices/esp-1").mock(
            return_value=httpx.Response(200, json=_hub_device_record("esp-1"))
        )
        view = await orchestrator.unbind_device("esp-1")
    assert view.binding is None  # no exception


# ---- enable / disable ----------------------------------------------------


async def test_set_enabled_calls_hub_and_refreshes_config(
    orchestrator: DeviceOrchestrator,
) -> None:
    with respx.mock(base_url=HUB_URL) as rsx:
        enable = rsx.post(
            "/api/admin/devices/esp-1/enable",
            params={"enabled": "false"},
        ).mock(
            return_value=httpx.Response(
                200, json=_hub_device_record("esp-1", enabled=False),
            )
        )
        refresh = rsx.post("/api/admin/devices/esp-1/commands").mock(
            return_value=httpx.Response(200, json=_hub_command_response("esp-1"))
        )
        view = await orchestrator.set_device_enabled("esp-1", enabled=False)
    assert enable.called
    assert refresh.called
    assert view.enabled is False


async def test_wake_disabled_device_rejects(
    orchestrator: DeviceOrchestrator,
) -> None:
    from eidolon_admin_server.app.registry.schemas.device import DeviceBinding

    await orchestrator._bindings.put(
        "esp-1",
        DeviceBinding(agent_id="ag-1", bound_at=datetime.now(timezone.utc)),
    )
    with respx.mock(base_url=HUB_URL) as rsx:
        rsx.get("/api/admin/devices/esp-1").mock(
            return_value=httpx.Response(
                200, json=_hub_device_record("esp-1", enabled=False),
            )
        )
        with pytest.raises(DeviceDisabled):
            await orchestrator.wake_device("esp-1")


# ---- unregister (cross-project cleanup) -----------------------------------


async def test_unregister_clears_binding_and_calls_hub(
    orchestrator: DeviceOrchestrator,
) -> None:
    from eidolon_admin_server.app.registry.schemas.device import DeviceBinding

    await orchestrator._bindings.put(
        "esp-1",
        DeviceBinding(agent_id="ag-1", bound_at=datetime.now(timezone.utc)),
    )
    with respx.mock(base_url=HUB_URL) as rsx:
        rsx.delete("/api/admin/devices/esp-1").mock(
            return_value=httpx.Response(
                200,
                json={
                    "device_id": "esp-1", "existed": True,
                    "presence_cleared": True,
                },
            )
        )
        result = await orchestrator.unregister_device("esp-1")
    assert result["existed"] is True
    assert await orchestrator._bindings.get("esp-1") is None


# ---- unbind_all_referring_to (agent cascade hook) -------------------------


async def test_unbind_all_referring_to_finds_and_clears(
    orchestrator: DeviceOrchestrator,
) -> None:
    """When an agent is deleted, all devices bound to it must be unbound."""
    from eidolon_admin_server.app.registry.schemas.device import DeviceBinding
    now = datetime.now(timezone.utc)
    # 2 devices reference ag-1, 1 references ag-2
    await orchestrator._bindings.put(
        "d1", DeviceBinding(agent_id="ag-1", bound_at=now)
    )
    await orchestrator._bindings.put(
        "d2", DeviceBinding(agent_id="ag-1", bound_at=now)
    )
    await orchestrator._bindings.put(
        "d3", DeviceBinding(agent_id="ag-2", bound_at=now)
    )

    affected = await orchestrator.unbind_all_referring_to("ag-1")
    assert set(affected) == {"d1", "d2"}
    # ag-2's d3 untouched
    assert (await orchestrator._bindings.get("d3")) is not None
    # ag-1's pointers gone
    assert (await orchestrator._bindings.get("d1")) is None
    assert (await orchestrator._bindings.get("d2")) is None


# ---- router (HTTP) -------------------------------------------------------


@pytest.fixture
async def client(
    orchestrator: DeviceOrchestrator,
) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.state.device_orchestrator = orchestrator
    app.include_router(devices_router, prefix="/api")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", trust_env=False
    ) as c:
        yield c


async def test_http_list_envelope(client: httpx.AsyncClient) -> None:
    with respx.mock(base_url=HUB_URL) as rsx:
        rsx.get("/api/admin/devices").mock(
            return_value=httpx.Response(200, json={"devices": []})
        )
        rsx.get("/api/admin/discovery").mock(
            return_value=httpx.Response(200, json=_hub_discovery_response())
        )
        r = await client.get("/api/devices")
    assert r.status_code == 200
    assert r.json() == {
        "devices": [],
        "hub_available": True,
        "discovery": _hub_discovery_response(),
    }


async def test_http_list_envelope_when_hub_down(
    client: httpx.AsyncClient,
) -> None:
    with respx.mock(base_url=HUB_URL) as rsx:
        rsx.get("/api/admin/devices").mock(side_effect=httpx.ConnectError("down"))
        r = await client.get("/api/devices")
    assert r.status_code == 200
    body = r.json()
    assert body["devices"] == []
    assert body["hub_available"] is False
    assert body["discovery"] is None


async def test_http_bind_400_for_missing_agent(
    client: httpx.AsyncClient,
) -> None:
    with respx.mock(base_url=HUB_URL) as rsx:
        rsx.get("/api/admin/devices/esp-1").mock(
            return_value=httpx.Response(200, json=_hub_device_record("esp-1"))
        )
        r = await client.post(
            "/api/devices/esp-1/bind", json={"agent_id": "ag-nonexistent"}
        )
    assert r.status_code == 400
    assert "agent 'ag-nonexistent'" in r.json()["detail"]


async def test_http_set_enabled(client: httpx.AsyncClient) -> None:
    with respx.mock(base_url=HUB_URL) as rsx:
        rsx.post(
            "/api/admin/devices/esp-1/enable",
            params={"enabled": "false"},
        ).mock(
            return_value=httpx.Response(
                200, json=_hub_device_record("esp-1", enabled=False),
            )
        )
        rsx.post("/api/admin/devices/esp-1/commands").mock(
            return_value=httpx.Response(200, json=_hub_command_response("esp-1"))
        )
        r = await client.post("/api/devices/esp-1/enable", params={"enabled": False})
    assert r.status_code == 200
    assert r.json()["enabled"] is False


async def test_http_503_when_orchestrator_missing() -> None:
    app = FastAPI()
    app.state.device_orchestrator = None
    app.include_router(devices_router, prefix="/api")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/api/devices")
    assert r.status_code == 503
