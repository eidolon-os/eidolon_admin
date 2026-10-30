from types import SimpleNamespace
from unittest.mock import AsyncMock
import httpx
import pytest
from fastapi import FastAPI, HTTPException
from eidolon_admin_server.local_api.management.voice_preview import register_voice_preview_routes


@pytest.mark.asyncio
async def test_preview_authentication_owner_scope_and_no_runtime_side_effects():
    backend = SimpleNamespace(voice_preview=AsyncMock(return_value={
        'key': 'a' * 64, 'state': 'generating', 'audio_available': False, 'voice': 'configured'}))
    async def owner(authorization):
        if authorization != 'Bearer controller':
            raise HTTPException(401)
        return 'owner-a'
    app = FastAPI()
    register_voice_preview_routes(app, backend=backend, authenticated_owner=owner)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        body = {'request_id': 'preview-one'}
        assert (await client.post('/api/management/v1/voice-preview', json=body)).status_code == 401
        backend.voice_preview.assert_not_called()
        response = await client.post('/api/management/v1/voice-preview?owner_id=forged',
            headers={'Authorization': 'Bearer controller'}, json=body)
        assert response.status_code == 200
        assert backend.voice_preview.call_args.kwargs == {
            'owner_id': 'owner-a', 'payload': {'request_id': 'preview-one', 'regenerate': False}}
        assert (await client.post('/api/management/v1/voice-preview',
            headers={'Authorization': 'Bearer controller'}, json=body | {'owner_id': 'forged'})).status_code == 422
        backend.voice_preview.return_value = {'pcm_base64': 'AAAB'}
        response = await client.get('/api/management/v1/voice-preview/' + 'a'*64 + '/audio',
            headers={'Authorization': 'Bearer controller'})
        assert response.status_code == 200
        assert response.headers['cache-control'] == 'no-store'
        assert backend.voice_preview.call_args.kwargs['owner_id'] == 'owner-a'
