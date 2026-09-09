from datetime import UTC, datetime

import httpx
import pytest
from eidolon_sdk.system.v1.host_monitor import HostMonitorWire, MonitorMemory, MonitorProcessor

from eidolon_admin_server.app.host_services.client import HostServiceClient
from eidolon_admin_server.app.host_services.errors import HostServiceError
from eidolon_admin_server.local_api.host_services import AdminHostServicesClient
from eidolon_admin_server.bootstrap.control import BootstrapControlClient
from tests.test_local_host_services import _app, _authenticate, _HostServicesPort, _CONTROLLER_ID

pytestmark = pytest.mark.asyncio


def snapshot():
    return HostMonitorWire(
        observed_at=datetime(2026, 9, 9, tzinfo=UTC),
        hostname="board",
        cpu=MonitorProcessor(device_id="cpu", model="RK3588"),
        memory=MonitorMemory(),
    )


async def test_both_proxy_clients_validate_and_preserve_snapshot():
    data = snapshot().model_dump(mode="json")
    seen = []

    async def handler(request):
        seen.append(request)
        return httpx.Response(200, json=data)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        system = HostServiceClient(base_url="http://system", timeout_seconds=2, client=http)
        local = AdminHostServicesClient(
            base_url="http://admin", service_token="test-token", timeout_seconds=2, client=http
        )
        assert await system.read_monitor() == snapshot()
        assert await local.read_monitor() == snapshot()
    assert [r.url.path for r in seen] == ["/api/system/v1/monitor", "/api/host/monitor"]
    assert seen[1].headers["Authorization"] == "Bearer test-token"


async def test_invalid_upstream_snapshot_is_rejected():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"hostname": "board"}))
    ) as http:
        client = HostServiceClient(base_url="http://system", timeout_seconds=2, client=http)
        with pytest.raises(HostServiceError):
            await client.read_monitor()


async def test_controller_auth_precedes_snapshot_and_response_is_not_cached(tmp_path, monkeypatch):
    calls = []

    class Host(_HostServicesPort):
        async def read_monitor(self):
            calls.append("read")
            return snapshot()

    async def bootstrap_request(self, operation, **params):
        if operation in {"controller.authenticate", "controller.validate"}:
            return {
                "contract_version": "1",
                "controller_id": _CONTROLLER_ID,
                "owner_id": "owner-1",
                "reset_epoch": 0,
            }
        raise AssertionError(operation)

    monkeypatch.setattr(BootstrapControlClient, "request", bootstrap_request)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app(tmp_path, Host())), base_url="https://local.test"
    ) as client:
        refused = await client.get("/api/management/v1/host/monitor")
        assert refused.status_code == 401
        assert calls == []
        auth = await _authenticate(client)
        answer = await client.get("/api/management/v1/host/monitor", headers=auth)
        assert answer.status_code == 200
        assert answer.headers["cache-control"] == "no-store"
        assert answer.json()["hostname"] == "board"
        assert calls == ["read"]
