import httpx
import pytest
from fastapi import FastAPI, HTTPException
from eidolon_admin_server.local_api.management.teams import register_team_routes


@pytest.mark.asyncio
async def test_team_scope_is_derived_not_supplied_by_client():
    calls = []
    class Backend:
        async def teams(self, **kwargs):
            calls.append(kwargs)
            return {"teams": [], "next_cursor": None}
    async def owner(authorization):
        if authorization != "Bearer controller":
            raise HTTPException(401)
        return "owner-a"
    async def host():
        return "host-a"
    app = FastAPI()
    register_team_routes(app, backend=Backend(), authenticated_owner=owner,
                         authenticated_host=host)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                base_url="http://test") as client:
        assert (await client.get('/api/management/v1/teams')).status_code == 401
        assert calls == []
        response = await client.get('/api/management/v1/teams?owner_id=forged&host_id=forged',
                                    headers={"Authorization": "Bearer controller"})
        assert response.status_code == 200
        assert calls == [dict(owner_id="owner-a", host_id="host-a", after="", limit=50)]


@pytest.mark.asyncio
async def test_saved_run_http_freezes_revision_and_rejects_client_configuration():
    from types import SimpleNamespace
    from eidolon_sdk.biz.control.coordination import CoordinationSelection
    from tests.test_saved_team_runs import saved, snapshot, status
    actions = []
    record = snapshot() | {'selection': None}
    class Backend:
        async def team_starts(self, **kwargs):
            assert kwargs['owner_id'] == 'owner-a'
            assert kwargs['host_id'] == 'host-a'
            assert kwargs['team_id'] == 'team'
            action = kwargs['action']; actions.append(action)
            if action == 'reserve':
                assert kwargs['payload'] == {'expected_revision': 2, 'session_id': 'run'}
                return saved()
            if action == 'snapshot':
                record['selection'] = kwargs['payload']
            if action == 'confirmed-closed':
                record.update(closed=True, terminal_status=kwargs['payload'])
            return record
    class Devices:
        async def resolve_team(self, **kwargs):
            assert kwargs['configuration'].name == saved()['configuration']['name']
            return CoordinationSelection.model_validate(snapshot()['selection'])
        async def role_group(self, **kwargs):
            if kwargs['action'] == 'open':
                assert actions == ['reserve', 'get', 'snapshot']
                assert kwargs['selection'].model_dump(mode='json') == record['selection']
            return status('closed' if kwargs['action'] == 'close' else 'ready')
    async def authenticated(authorization):
        if authorization != 'Bearer controller': raise HTTPException(401)
        return SimpleNamespace(owner_id='owner-a')
    async def owner(authorization): return (await authenticated(authorization)).owner_id
    async def host(): return 'host-a'
    app = FastAPI()
    register_team_routes(app, backend=Backend(), authenticated_owner=owner,
        authenticated_host=host, authenticate_session=authenticated, devices=Devices())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        path = '/api/management/v1/teams/team/runs'
        body = {'expected_revision': 2, 'session_id': 'run'}
        headers = {'Authorization': 'Bearer controller'}
        assert (await client.post(path, json=body)).status_code == 401
        for extra in ({'owner_id': 'forged'}, {'configuration': saved()['configuration']}):
            assert (await client.post(path, json=body | extra, headers=headers)).status_code == 422
        response = await client.post(path, json=body, headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()['state'] == 'ready'
        response = await client.post(path + '/run/close', headers=headers)
        assert response.status_code == 200, response.text
        assert record['closed']
        assert record['terminal_status']['state'] == 'closed'


@pytest.mark.asyncio
async def test_run_history_query_stays_in_authenticated_scope():
    from types import SimpleNamespace
    calls = []
    class Backend:
        async def team_starts(self, **kwargs):
            calls.append(kwargs)
            return {'starts': [], 'next_cursor': 'run-next'}
    async def authenticated(authorization):
        if authorization != 'Bearer controller': raise HTTPException(401)
        return SimpleNamespace(owner_id='owner-a')
    async def owner(authorization): return (await authenticated(authorization)).owner_id
    async def host(): return 'host-a'
    app = FastAPI()
    register_team_routes(app, backend=Backend(), authenticated_owner=owner,
        authenticated_host=host, authenticate_session=authenticated, devices=object())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get('/api/management/v1/teams/team/runs?include_closed=true&after=run-z&limit=2',
                                    headers={'Authorization': 'Bearer controller'})
        assert response.status_code == 200, response.text
        assert response.json()['next_cursor'] == 'run-next'
        assert calls == [dict(owner_id='owner-a', host_id='host-a', team_id='team',
                              action='list', include_closed=True, after='run-z', limit=2)]
