"""Authenticated on-demand voice preview; never starts or changes a conversation."""
from fastapi import APIRouter, Header, Response
from eidolon_sdk.biz.control.voice_preview import VoicePreviewRequest, VoicePreviewStatus
from eidolon_sdk.biz.voiceprint import VoiceAudioRequest
from .router import ManagementBackendError, _refused


def register_voice_preview_routes(app, *, backend, authenticated_owner):
    router = APIRouter(prefix='/api/management/v1/voice-preview', tags=['management'])

    async def invoke(authorization, **kwargs):
        owner = await authenticated_owner(authorization)
        try:
            return await backend.voice_preview(owner_id=owner, **kwargs)
        except ManagementBackendError as exc:
            raise _refused(exc) from exc

    @router.post('', response_model=VoicePreviewStatus)
    async def preview(body: VoicePreviewRequest, authorization: str | None = Header(default=None)):
        return await invoke(authorization, payload=body.model_dump(mode='json'))

    @router.get('/{key}/audio', response_model=VoiceAudioRequest)
    async def audio(key: str, response: Response, authorization: str | None = Header(default=None)):
        response.headers['Cache-Control'] = 'no-store'
        return await invoke(authorization, key=key)

    app.include_router(router)
