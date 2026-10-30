from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from eidolon_sdk.biz.control.teams import TeamStartRequest
from eidolon_sdk.device_foundation.v1.testing import named_device_instance_id as device
from eidolon_admin_server.local_api.management.team_runs import SavedTeamRuns


def saved():
    return dict(team_id='team', revision=2, configuration=dict(name='朋友',
        input_device_id=device('input'), members=[dict(companion_id='a', output_device_id=device('output'))]))


def snapshot():
    from eidolon_sdk.biz.control.teams import TeamConfiguration
    from eidolon_sdk.device_foundation.v1 import DeviceRef
    config = TeamConfiguration.model_validate(saved()['configuration'])
    refs = {id: DeviceRef(device_instance_id=id, owner_domain_id='owner-domain',
        owner_domain_generation=1, claim_generation=1, trust_epoch=1)
        for id in (config.input_device_id, config.members[0].output_device_id)}
    selection = config.resolve(session_id='run', devices=refs,
        attachments={config.members[0].output_device_id: 'a'})
    return dict(team_id='team', session_id='run', revision=2, closed=False,
                selection=selection.model_dump(mode='json'))


def status(state):
    return dict(session_id='run', state=state, scenario='ip_role_group', completion_basis='native_playout')


@pytest.mark.asyncio
@pytest.mark.parametrize('state', ['ready', 'failed', 'closing', 'closed'])
async def test_only_confirmed_close_releases_start_intent(state):
    assets = SimpleNamespace(team_starts=AsyncMock(side_effect=[saved(), snapshot(), {'team_id': 'team'}]))
    devices = SimpleNamespace(role_group=AsyncMock(return_value=status(state)))
    service = SavedTeamRuns(assets, devices)
    answer = await service.start(session=SimpleNamespace(owner_id='owner'), host_id='host',
        team_id='team', request=TeamStartRequest(expected_revision=2, session_id='run'))
    assert answer.state == state
    assert devices.role_group.call_args.kwargs['selection'].session_id == 'run'
    assert assets.team_starts.await_count == (3 if state == 'closed' else 2)


@pytest.mark.asyncio
async def test_lost_start_response_never_releases_or_reopens():
    assets = SimpleNamespace(team_starts=AsyncMock(side_effect=[saved(), snapshot()]))
    devices = SimpleNamespace(role_group=AsyncMock(side_effect=TimeoutError()))
    with pytest.raises(TimeoutError):
        await SavedTeamRuns(assets, devices).start(session=SimpleNamespace(owner_id='owner'),
            host_id='host', team_id='team',
            request=TeamStartRequest(expected_revision=2, session_id='run'))
    assert assets.team_starts.await_count == 2
    assert devices.role_group.await_count == 1


@pytest.mark.asyncio
async def test_mismatched_close_response_never_releases():
    assets = SimpleNamespace(team_starts=AsyncMock(side_effect=[saved(), snapshot()]))
    devices = SimpleNamespace(role_group=AsyncMock(return_value=status('closed') | {'session_id': 'other'}))
    with pytest.raises(ValueError, match='MISMATCH'):
        await SavedTeamRuns(assets, devices).start(session=SimpleNamespace(owner_id='owner'),
            host_id='host', team_id='team',
            request=TeamStartRequest(expected_revision=2, session_id='run'))
    assert assets.team_starts.await_count == 2


@pytest.mark.asyncio
async def test_closed_run_replay_does_not_call_provider():
    assets = SimpleNamespace(team_starts=AsyncMock(return_value=dict(
        team_id='team', session_id='run', revision=2, closed=True)))
    devices = SimpleNamespace(role_group=AsyncMock())
    result = await SavedTeamRuns(assets, devices).inspect(session=SimpleNamespace(owner_id='owner'),
        host_id='host', team_id='team', session_id='run', close=True)
    assert result.state == 'closed'
    devices.role_group.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('close_state', ['closed', 'closing', 'failed'])
async def test_rejected_start_requires_provider_fence_before_release(close_state):
    from eidolon_admin_server.local_api.management.router import ManagementBackendError, refusal_for_status
    refused = ManagementBackendError('Companion occupied', status_code=409,
        refusal=refusal_for_status(409, 'Companion occupied'))
    assets = SimpleNamespace(team_starts=AsyncMock(side_effect=[saved(), snapshot(), {'team_id': 'team'}]))
    devices = SimpleNamespace(role_group=AsyncMock(side_effect=[refused, status(close_state)]))
    with pytest.raises(ManagementBackendError) as error:
        await SavedTeamRuns(assets, devices).start(session=SimpleNamespace(owner_id='owner'),
            host_id='host', team_id='team', request=TeamStartRequest(expected_revision=2, session_id='run'))
    assert error.value is refused
    assert [c.kwargs['action'] for c in devices.role_group.call_args_list] == ['open', 'close']
    assert assets.team_starts.await_count == (3 if close_state == 'closed' else 2)
    if close_state == 'closed':
        assert assets.team_starts.call_args.kwargs['payload']['error'] == 'TEAM_START_REFUSED'


@pytest.mark.asyncio
async def test_unreachable_close_after_rejection_keeps_intent_and_original_error():
    from eidolon_admin_server.local_api.management.router import ManagementBackendError, refusal_for_status
    refused = ManagementBackendError('binding changed', status_code=409,
        refusal=refusal_for_status(409, 'binding changed'))
    assets = SimpleNamespace(team_starts=AsyncMock(side_effect=[saved(), snapshot()]))
    devices = SimpleNamespace(role_group=AsyncMock(side_effect=[refused, TimeoutError()]))
    with pytest.raises(ManagementBackendError) as error:
        await SavedTeamRuns(assets, devices).start(session=SimpleNamespace(owner_id='owner'),
            host_id='host', team_id='team', request=TeamStartRequest(expected_revision=2, session_id='run'))
    assert error.value is refused
    assert assets.team_starts.await_count == 2


@pytest.mark.asyncio
async def test_first_start_persists_snapshot_before_dispatch_and_reuses_winner():
    from eidolon_sdk.biz.control.coordination import CoordinationSelection
    accepted = snapshot()
    candidate = CoordinationSelection.model_validate(accepted['selection'])
    candidate = candidate.model_copy(update={'input_device': candidate.input_device.model_copy(update={'trust_epoch': 2})})
    assets = SimpleNamespace(team_starts=AsyncMock(side_effect=[saved(),
        accepted | {'selection': None}, accepted]))
    devices = SimpleNamespace(resolve_team=AsyncMock(return_value=candidate),
        role_group=AsyncMock(return_value=status('ready')))
    await SavedTeamRuns(assets, devices).start(session=SimpleNamespace(owner_id='owner'),
        host_id='host', team_id='team', request=TeamStartRequest(expected_revision=2, session_id='run'))
    assert [c.kwargs['action'] for c in assets.team_starts.call_args_list] == ['reserve', 'get', 'snapshot']
    assert devices.role_group.call_args.kwargs['selection'].input_device.trust_epoch == 1


@pytest.mark.asyncio
async def test_closed_replay_preserves_original_failure_cause_without_provider():
    terminal = status('closed') | {'error': 'ENDPOINT_REACHABILITY_LOST'}
    assets = SimpleNamespace(team_starts=AsyncMock(return_value=snapshot() | {
        'closed': True, 'terminal_status': terminal}))
    devices = SimpleNamespace(role_group=AsyncMock())
    result = await SavedTeamRuns(assets, devices).inspect(session=SimpleNamespace(owner_id='owner'),
        host_id='host', team_id='team', session_id='run')
    assert result.error == terminal['error']
    devices.role_group.assert_not_called()


@pytest.mark.asyncio
async def test_conflict_locator_uses_active_snapshot_not_edited_configuration():
    from eidolon_sdk.biz.control.teams import TeamRevision
    wanted = saved() | {'team_id': 'wanted'}
    changed = saved() | {'team_id': 'other', 'configuration': {
        **saved()['configuration'], 'name': '另一团队',
        'members': [{'companion_id': 'changed', 'output_device_id': device('changed-device')}]}}
    record = snapshot() | {'team_id': 'other'}
    # Same Companion, different device: occupancy follows the Companion too.
    record['selection']['members'][0]['output_device']['device_instance_id'] = device('elsewhere')
    record['selection']['input_device']['device_instance_id'] = device('else-input')
    async def teams(**kwargs):
        assert kwargs['owner_id'] == 'owner' and kwargs['host_id'] == 'host'
        return wanted if kwargs.get('team_id') else {'teams': [changed]}
    async def starts(**kwargs):
        assert kwargs['owner_id'] == 'owner' and kwargs['host_id'] == 'host'
        return {'starts': [{k: record[k] for k in ('team_id','session_id','revision','closed')}]} if kwargs['action']=='list' else record
    assets = SimpleNamespace(teams=teams, team_starts=starts)
    devices = SimpleNamespace(role_group=AsyncMock(return_value=status('ready')))
    answer = await SavedTeamRuns(assets, devices).conflicts(
        session=SimpleNamespace(owner_id='owner'), host_id='host', team_id='wanted')
    assert len(answer.conflicts) == 1
    assert answer.conflicts[0].team_id == 'other'
    assert answer.conflicts[0].companion_ids == ('a',)
    assert answer.conflicts[0].device_ids == ()
