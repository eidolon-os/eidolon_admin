import httpx
import pytest
from tests.test_shared_device_sessions import Devices, Admission, SECOND
from tests.test_local_device_companion import _app, _headers, _controller_principal, _DEVICE

class ConversationDevices(Devices):
    async def device_conversation(self, *, payload, action):
        self.commands.append(payload)
        assert "device.conversation.control" in payload.actor.granted_scopes
        return {"session_id": payload.selection.session_id if action == "open" else payload.session_id,
                "state": {"open": "preparing", "status": "ready", "close": "closed"}[action], "error": ""}

@pytest.mark.asyncio
async def test_authorized_selection_status_and_stop(tmp_path, monkeypatch):
    _controller_principal(monkeypatch)
    devices = ConversationDevices()
    app = _app(tmp_path, devices, Admission())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        path = "/api/management/v1/device-conversations"
        body = dict(session_id="dialogue", input_device_id=_DEVICE, output_device_id=SECOND,
                    target_companion_id="selected-companion")
        assert (await client.post(f"{path}/open", json=body)).status_code == 401
        headers = await _headers(client)
        response = await client.post(f"{path}/open", json=body, headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["state"] == "preparing"
        command = devices.commands[-1]
        assert command.selection.input_device.device_instance_id == _DEVICE
        assert command.selection.output_device.device_instance_id == SECOND
        assert command.selection.target_companion_id == "selected-companion"
        for action, state in (("status", "ready"), ("close", "closed")):
            response = await client.post(f"{path}/{action}", json={"session_id": "dialogue"}, headers=headers)
            assert response.status_code == 200, response.text
            assert response.json()["state"] == state
        for invalid in (body | {"owner_id": "forged"}, body | {"output_device_id": _DEVICE}):
            assert (await client.post(f"{path}/open", json=invalid, headers=headers)).status_code == 422
        assert len(devices.commands) == 3
