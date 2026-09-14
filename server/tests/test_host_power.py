import json
from types import SimpleNamespace

import httpx
import pytest
from eidolon_admin_server.app.host_services.client import HostServiceClient
from eidolon_admin_server.app.host_services.errors import HostServiceError
from eidolon_admin_server.app.host_services.router import router
from eidolon_admin_server.bootstrap.control import BootstrapControlClient
from eidolon_admin_server.local_api.host_services import AdminHostServicesClient
from eidolon_sdk.system.v1 import HostPowerOffAccepted, HostPowerStatusWire
from fastapi import FastAPI

from tests.test_local_host_services import (
    _CONTROLLER_ID,
    _app,
    _authenticate,
    _HostServicesPort,
)

pytestmark = pytest.mark.asyncio


class Host(_HostServicesPort):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def read_power(self):
        self.calls.append("read")
        return HostPowerStatusWire(can_power_off=True)

    async def power_off(self, *, request_id):
        self.calls.append(request_id)
        return HostPowerOffAccepted(request_id=request_id)


async def test_both_proxies_preserve_request_identity_and_use_fixed_routes():
    seen = []

    async def handler(request):
        seen.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"can_power_off": True})
        return httpx.Response(
            202,
            json=HostPowerOffAccepted(
                request_id=json.loads(request.content)["request_id"]
            ).model_dump(),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        for client in (
            HostServiceClient(base_url="http://system", timeout_seconds=2, client=http),
            AdminHostServicesClient(
                base_url="http://admin",
                service_token="service",
                timeout_seconds=2,
                client=http,
            ),
        ):
            assert (await client.read_power()).can_power_off
            assert (
                await client.power_off(request_id="power-one")
            ).request_id == "power-one"
    assert [r.url.path for r in seen] == [
        "/api/system/v1/power",
        "/api/system/v1/poweroff",
        "/api/host/power",
        "/api/host/poweroff",
    ]
    assert seen[-1].headers["Authorization"] == "Bearer service"
    assert json.loads(seen[-1].content) == {"request_id": "power-one"}


@pytest.mark.parametrize(
    "status,body",
    [
        (
            202,
            {
                "request_id": "wrong",
                "operation": "system.poweroff",
                "status": "accepted",
            },
        ),
        (403, {"detail": "denied"}),
        (502, {}),
    ],
)
async def test_invalid_or_refused_answer_never_becomes_acceptance(status, body):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(status, json=body))
    ) as http:
        client = HostServiceClient(
            base_url="http://system", timeout_seconds=2, client=http
        )
        with pytest.raises(HostServiceError):
            await client.power_off(request_id="one")


async def test_proxy_does_not_retry_lost_power_response():
    calls = []

    async def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("gone")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(HostServiceError):
            await HostServiceClient(
                base_url="http://system", timeout_seconds=2, client=http
            ).power_off(request_id="one")
    assert len(calls) == 1


async def test_admin_power_routes_require_service_credential():
    host = Host()
    app = FastAPI()
    app.state.settings = SimpleNamespace(local_api_service_token="service")
    app.state.host_services = host
    app.include_router(router, prefix="/api")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://admin"
    ) as client:
        assert (await client.get("/api/host/power")).status_code == 401
        assert (
            await client.post("/api/host/poweroff", json={"request_id": "one"})
        ).status_code == 401
        assert host.calls == []
        response = await client.post(
            "/api/host/poweroff",
            headers={"Authorization": "Bearer service"},
            json={"request_id": "one"},
        )
        assert response.status_code == 202
        assert host.calls == ["one"]


async def test_controller_auth_precedes_power_and_revoked_controller_cannot_execute(
    tmp_path, monkeypatch
):
    revoked = False

    async def bootstrap_request(self, operation, **params):
        if operation in {"controller.authenticate", "controller.validate"}:
            if revoked:
                from eidolon_admin_server.bootstrap.control import BootstrapControlError

                raise BootstrapControlError("revoked", code="ControllerAuthenticationRejected")
            return {
                "contract_version": "1",
                "controller_id": _CONTROLLER_ID,
                "owner_id": "owner-1",
                "reset_epoch": 0,
            }
        raise AssertionError(operation)

    monkeypatch.setattr(BootstrapControlClient, "request", bootstrap_request)
    host = Host()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app(tmp_path, host)),
        base_url="https://local.test",
    ) as client:
        assert (await client.get("/api/management/v1/host/power")).status_code == 401
        assert (
            await client.post(
                "/api/management/v1/host/poweroff", json={"request_id": "one"}
            )
        ).status_code == 401
        assert host.calls == []
        auth = await _authenticate(client)
        result = await client.get("/api/management/v1/host/power", headers=auth)
        assert (
            result.status_code == 200 and result.headers["cache-control"] == "no-store"
        )
        result = await client.post(
            "/api/management/v1/host/poweroff", headers=auth, json={"request_id": "one"}
        )
        assert result.status_code == 202 and result.json()["request_id"] == "one"
        assert host.calls == ["read", "one"]
        revoked = True
        result = await client.post(
            "/api/management/v1/host/poweroff", headers=auth, json={"request_id": "two"}
        )
        assert result.status_code == 401
        assert host.calls == ["read", "one"]
