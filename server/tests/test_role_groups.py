import httpx
import pytest
from tests.test_shared_device_sessions import Devices, Admission, SECOND
from tests.test_local_device_companion import _app, _headers, _controller_principal, _DEVICE

class TeamDevices(Devices):
    async def role_group(self, *, payload, action):
        self.commands.append(payload)
        assert 'device.conversation.control' in payload.actor.granted_scopes
        return dict(session_id=payload.selection.session_id if action == 'open' else payload.session_id,
            state={'open':'preparing', 'status':'ready', 'close':'closed'}[action], error='',
            scenario='ip_role_group', completion_basis='native_playout')

@pytest.mark.asyncio
async def test_owner_team_routes_resolve_refs_and_attached_companion(tmp_path, monkeypatch):
    _controller_principal(monkeypatch)
    devices = TeamDevices()
    devices.companion_id = "c_01"
    devices.revision = 1
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app(tmp_path, devices, Admission())), base_url='http://test') as client:
        path='/api/management/v1/role-groups'
        body=dict(session_id='team-test', input_device_id=_DEVICE, output_device_ids=[SECOND],
            roles=[dict(output_device_id=SECOND, role=dict(name='孙悟空'))])
        assert (await client.post(path+'/open', json=body)).status_code == 401
        headers=await _headers(client)
        response=await client.post(path+'/open', json=body, headers=headers)
        assert response.status_code == 200, response.text
        command=devices.commands[-1]
        assert command.selection.input_device.device_instance_id == _DEVICE
        assert command.selection.members[0].output_device.device_instance_id == SECOND
        assert command.selection.members[0].role.name == "孙悟空"
        assert command.selection.members[0].companion_id
        for action, state in [('status','ready'),('close','closed')]:
            response=await client.post(path+'/'+action, json={'session_id':'team-test'}, headers=headers)
            assert response.status_code == 200, response.text
            assert response.json()['state'] == state
        for invalid in [body | {'roles':[dict(output_device_id=_DEVICE,role=dict(name='八戒'))]},
                        body | {'roles':body['roles']*2},
                        body | {'roles':[dict(output_device_id=SECOND,role=dict(name='  '))]},
                        body | {'owner_id':'forged'},body | {'output_device_ids':[_DEVICE]},body | {'output_device_ids':[SECOND,SECOND]}]:
            assert (await client.post(path+'/open', json=invalid, headers=headers)).status_code == 422
        assert len(devices.commands) == 3
