"""Credential-isolated relay to Channel's configured-voice preview capability."""
from urllib.parse import quote
import httpx
from fastapi import APIRouter, Depends, Request, HTTPException
from eidolon_sdk.biz.control.voice_preview import VoicePreviewRequest, VoicePreviewStatus
from eidolon_sdk.biz.voiceprint import VoiceAudioRequest
from eidolon_admin_server.app.service_auth import require_local_api_credential
from eidolon_admin_server.app.gateway.proxy import upstream_auth_headers

router = APIRouter(prefix='/internal/v1/management/voice-preview',
    dependencies=[Depends(require_local_api_credential)])


async def _relay(request, owner_id, *, key=None, payload=None):
    service = request.app.state.registry.get('channel-provider')
    if service is None or not service.base_url:
        raise HTTPException(503, detail='Voice preview is unavailable')
    suffix = '/' + quote(key, safe='') + '/audio' if key is not None else ''
    url = (service.base_url.rstrip('/') + (service.upstream_prefix or '').rstrip('/')
        + '/v1/owners/' + quote(owner_id, safe='') + '/voice-preview' + suffix)
    try:
        response = await request.app.state.http_client.request('GET' if key else 'POST', url,
            json=payload, headers=upstream_auth_headers(service), timeout=10)
        response.raise_for_status()
        model = VoiceAudioRequest if key else VoicePreviewStatus
        return model.model_validate(response.json())
    except httpx.HTTPStatusError as exc:
        raise HTTPException(404 if exc.response.status_code == 404 else 503,
                            detail='Voice preview is unavailable') from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(503, detail='Voice preview is unavailable') from exc


@router.post('', response_model=VoicePreviewStatus)
async def preview(request: Request, owner_id: str, body: VoicePreviewRequest):
    return await _relay(request, owner_id, payload=body.model_dump(mode='json'))


@router.get('/{key}/audio', response_model=VoiceAudioRequest)
async def audio(request: Request, owner_id: str, key: str):
    return await _relay(request, owner_id, key=key)
