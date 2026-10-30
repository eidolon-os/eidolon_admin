import httpx
from fastapi import APIRouter, FastAPI, HTTPException
from eidolon_admin_server.local_api.management.voiceprints import register_voiceprint_routes


async def test_capture_uses_authenticated_owner_and_no_store():
    calls = []
    class Backend:
        async def voiceprint_call(self, **kwargs):
            calls.append(kwargs)
            if kwargs['suffix'] == '/captures':
                return {'capture_id':'a'*32,'server_urls':['ws://host:7880'],'token':'ephemeral'}
            if kwargs['suffix'].endswith('/finish'):
                return {'pcm_base64':'AAAA'}
            return {}
    async def auth(header):
        if header != 'Bearer controller': raise HTTPException(401)
        return 'real-owner'
    router = APIRouter()
    register_voiceprint_routes(router, Backend(), auth, lambda e: e)
    app = FastAPI()
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        assert (await client.post('/voiceprints/captures')).status_code == 401
        assert not calls
        client.headers['Authorization'] = 'Bearer controller'
        for method, path in [('POST','/captures'),('POST','/captures/'+'a'*32+'/finish'),('DELETE','/captures/'+'a'*32)]:
            result = await client.request(method, '/voiceprints'+path+'?owner_id=attacker')
            assert result.status_code == 200
            assert result.headers['cache-control'] == 'no-store'
            assert calls[-1]['owner_id'] == 'real-owner'
