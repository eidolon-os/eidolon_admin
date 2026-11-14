import httpx
import pytest
from tests.test_shared_device_sessions import Devices, Admission
from tests.test_local_device_companion import _app, _headers, _controller_principal, _DEVICE

class SessionDevices(Devices):
    async def device_session(self, *, payload, action):
        self.commands.append(payload)
        assert "device.conversation.control" in payload.actor.granted_scopes
        return {"device_id": payload.request.device.device_instance_id, "error": "",
                "conversation_id": payload.request.conversation_id,
                "state": {"open": "starting", "status": "in_conversation", "close": "standby"}[action]}

@pytest.mark.asyncio
async def test_owner_reads_starts_and_ends_the_devices_conversation(tmp_path, monkeypatch):
    _controller_principal(monkeypatch)
    devices = SessionDevices()
    app = _app(tmp_path, devices, Admission())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        path = "/api/management/v1/device-sessions"
        body = dict(device_id=_DEVICE)
        assert (await client.post(f"{path}/open", json=body)).status_code == 401
        headers = await _headers(client)
        for action, state in (("open", "starting"), ("status", "in_conversation"), ("close", "standby")):
            response = await client.post(f"{path}/{action}", json=body, headers=headers)
            assert response.status_code == 200, response.text
            assert response.json()["state"] == state
            assert devices.commands[-1].request.device.device_instance_id == _DEVICE
        scoped = await client.post(f"{path}/close", json=body | {"conversation_id": "lifted"}, headers=headers)
        assert scoped.json()["conversation_id"] == "lifted"
        for invalid in (body | {"owner_id": "forged"}, body | {"session_id": "mine"}):
            assert (await client.post(f"{path}/open", json=invalid, headers=headers)).status_code == 422
        assert (await client.post(f"{path}/restart", json=body, headers=headers)).status_code == 422
        assert len(devices.commands) == 4
