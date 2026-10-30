"""Product starts must reference saved assets; internal role-group core remains."""
import httpx
import pytest
from tests.test_shared_device_sessions import Devices, Admission
from tests.test_local_device_companion import _app, _headers, _controller_principal

@pytest.mark.asyncio
async def test_retired_free_configuration_routes_cannot_dispatch(tmp_path, monkeypatch):
    _controller_principal(monkeypatch)
    devices = Devices()
    app = _app(tmp_path, devices, Admission())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        headers = await _headers(client)
        for action in ('open', 'status', 'close'):
            response = await client.post('/api/management/v1/role-groups/' + action,
                json={'session_id': 'legacy'}, headers=headers)
            assert response.status_code == 404
    assert devices.commands == []
    assert '/api/management/v1/teams/{team_id}/runs' in app.openapi()['paths']
    assert not any('/role-groups/' in path for path in app.openapi()['paths'])
