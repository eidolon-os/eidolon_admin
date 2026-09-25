import httpx
import pytest
from eidolon_sdk.device_foundation.v1.testing import named_device_instance_id
from tests.test_local_device_companion import (
    _Devices,
    _Admission,
    _app,
    _headers,
    _controller_principal,
    _DEVICE,
    _BUSINESS_OWNER,
)
from tests.test_control_plane_clients import directory
from eidolon_admin_server.app.control_plane.clients import HubManagementClient

SECOND = named_device_instance_id("shared-second")


class Devices(_Devices):
    async def list_body_endpoints(self, owner_id):
        page = await super().list_body_endpoints(owner_id)
        second = type(page).model_validate_json(
            page.model_dump_json().replace(_DEVICE, SECOND)
        )
        return page.model_copy(update={"endpoints": page.endpoints + second.endpoints})

    async def shared_session(self, *, payload, action):
        self.commands.append(payload)
        assert str(payload.business_owner_id) == _BUSINESS_OWNER
        assert "device.shared-session.control" in payload.actor.granted_scopes
        return {
            "session_id": payload.selection.session_id
            if action == "open"
            else payload.session_id,
            "state": "transport_ready" if action == "open" else "closed",
        }


class Admission(_Admission):
    async def query_claims(self, *, payload):
        page = await super().query_claims(payload=payload)
        second = type(page).model_validate_json(
            page.model_dump_json().replace(_DEVICE, SECOND)
        )
        return page.model_copy(update={"items": page.items + second.items})


@pytest.mark.asyncio
async def test_product_session_resolves_refs_and_signing_authority_from_existing_owner(
    tmp_path, monkeypatch
):
    _controller_principal(monkeypatch)
    devices = Devices()
    app = _app(tmp_path, devices, Admission())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        body = {
            "session_id": "visit1",
            "device_ids": [_DEVICE, SECOND],
            "input_device_id": _DEVICE,
        }
        assert (
            await client.post("/api/management/v1/shared-sessions/open", json=body)
        ).status_code == 401
        headers = await _headers(client)
        response = await client.post(
            "/api/management/v1/shared-sessions/open", json=body, headers=headers
        )
        assert response.status_code == 200, response.text
        assert len(devices.commands[0].selection.devices) == 2
        response = await client.post(
            "/api/management/v1/shared-sessions/close",
            json={"session_id": "visit1"},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        body["owner_id"] = _BUSINESS_OWNER
        assert (
            await client.post(
                "/api/management/v1/shared-sessions/open", json=body, headers=headers
            )
        ).status_code == 422
        assert len(devices.commands) == 2


@pytest.mark.asyncio
async def test_hub_shared_transport_uses_directory_and_rejects_wrong_response():
    def handler(request):
        assert request.url.path == "/api/device-control/v1/shared-sessions/close"
        assert request.headers["authorization"] == "Bearer trusted"
        assert request.extensions["timeout"]["read"] == 35
        return httpx.Response(200, json={"session_id": "visit1", "state": "closed"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = HubManagementClient(
            directory=directory(), client=http, timeout_seconds=1
        )
        result = await client.shared_session(
            action="close",
            command={"session_id": "visit1"},
            authorization="Bearer trusted",
        )
        assert result["state"] == "closed"
