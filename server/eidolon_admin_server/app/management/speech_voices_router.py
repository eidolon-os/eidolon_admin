"""Internal routes for speech voices; the Local API is the only caller.

Answers carry provider references: deciding what a phone may see is the Local
API's job. ``owner_id`` arrives as a query parameter, as on every internal
management route.
"""

from __future__ import annotations

from typing import Annotated, Literal

from eidolon_sdk.biz.speech_voice import (
    DESIGN_PROMPT_MAX_CHARS,
    PREVIEW_TEXT_MAX_CHARS,
    PREVIEW_TEXT_MIN_CHARS,
    CompanionSpeechVoice,
    HostVoiceCatalog,
    OwnerVoice,
    OwnerVoiceConsent,
    OwnerVoiceUpdate,
    ReferenceCheck,
    ResolvedSpeechVoice,
)
from fastapi import APIRouter, Depends, Header, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from eidolon_admin_server.app.control_plane.errors import AuthorityFailure
from eidolon_admin_server.app.service_auth import require_local_api_credential

from .speech_voices import ChannelVoices, SpeechVoices, host_model

router = APIRouter(
    prefix="/internal/v1/management",
    tags=["management-internal"],
    dependencies=[Depends(require_local_api_credential)],
)

OperationId = Annotated[str, StringConstraints(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class VoiceChoice(Strict):
    voice_key: str | None
    expected_revision: int = Field(ge=0)


class VoicePreviewRequest(Strict):
    voice_key: str
    sample: int = Field(default=1, ge=1, le=2)


class VoiceDesignRequest(Strict):
    operation_id: OperationId
    name: Name
    prompt: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=DESIGN_PROMPT_MAX_CHARS)]
    preview_text: Annotated[str, StringConstraints(
        strip_whitespace=True, min_length=PREVIEW_TEXT_MIN_CHARS, max_length=PREVIEW_TEXT_MAX_CHARS)]


class VoiceCloneRequest(Strict):
    operation_id: OperationId
    name: Name
    consent: OwnerVoiceConsent
    reference_wav_base64: str


class CompanionVoiceInternal(Strict):
    record: CompanionSpeechVoice
    effective: ResolvedSpeechVoice | None


class OwnerVoicesInternal(Strict):
    voices: list[OwnerVoiceInternal]


class OwnerVoiceInternal(Strict):
    voice: OwnerVoice
    #: How this voice stands on this Host's current model.
    status: Literal["ready", "preparing", "rejected", "unavailable"]
    status_detail: str | None = None


OwnerVoicesInternal.model_rebuild()


def _voices(request: Request) -> SpeechVoices:
    data = request.app.state.control_plane.data
    return SpeechVoices(data=data, roster=data, channel=ChannelVoices.of(request.app))


async def _with_status(request: Request, voices: list[OwnerVoice]) -> list[OwnerVoiceInternal]:
    try:
        catalog, _ = await ChannelVoices.of(request.app).catalog()
    except AuthorityFailure:
        catalog = None
    current = host_model(catalog) if catalog else None
    out = []
    for voice in voices:
        binding = voice.binding_for(current.provider, current.model) if current else None
        if binding is None:
            status, detail = "unavailable", None
        else:
            status = {"ready": "ready", "deploying": "preparing"}.get(binding.status, "rejected")
            detail = binding.status_detail
        out.append(OwnerVoiceInternal(voice=voice, status=status, status_detail=detail))
    return out


@router.get("/speech-voices", response_model=HostVoiceCatalog,
            responses={304: {"description": "Not modified"}})
async def catalog(request: Request, response: Response, owner_id: str,
                  if_none_match: str | None = Header(default=None, alias="If-None-Match")):
    del owner_id  # the catalog is the Host's; the parameter only matches the internal convention
    current, etag = await ChannelVoices.of(request.app).catalog(if_none_match)
    if current is None:
        return Response(status_code=304, headers={"ETag": etag or ""})
    if etag:
        response.headers["ETag"] = etag
    return current


@router.post("/speech-voices/refresh", response_model=HostVoiceCatalog)
async def refresh(request: Request, owner_id: str):
    del owner_id
    return await ChannelVoices.of(request.app).refresh()


@router.post("/speech-voices/preview", response_class=Response,
             responses={200: {"content": {"audio/wav": {}}}})
async def preview(request: Request, owner_id: str, body: VoicePreviewRequest):
    data = await _voices(request).preview(owner_id, body.voice_key, body.sample)
    return Response(content=data, media_type="audio/wav", headers={"Cache-Control": "private, max-age=86400"})


@router.get("/companions/{companion_id}/speech-voice", response_model=CompanionVoiceInternal)
async def companion_voice(request: Request, companion_id: str, owner_id: str):
    result = await _voices(request).companion_voice(owner_id, companion_id)
    return CompanionVoiceInternal(record=result.record, effective=result.effective)


@router.put("/companions/{companion_id}/speech-voice", response_model=CompanionVoiceInternal)
async def choose(request: Request, companion_id: str, owner_id: str, body: VoiceChoice):
    result = await _voices(request).choose(owner_id, companion_id, body.voice_key, body.expected_revision)
    return CompanionVoiceInternal(record=result.record, effective=result.effective)


@router.get("/owner-voices", response_model=OwnerVoicesInternal)
async def owner_voices(request: Request, owner_id: str):
    return OwnerVoicesInternal(voices=await _with_status(request, await _voices(request).owner_voices(owner_id)))


@router.post("/owner-voices/designs", response_model=OwnerVoiceInternal)
async def design(request: Request, owner_id: str, body: VoiceDesignRequest):
    voice = await _voices(request).design(owner_id, operation_id=body.operation_id, name=body.name,
                                          prompt=body.prompt, preview_text=body.preview_text)
    return (await _with_status(request, [voice]))[0]


@router.post("/owner-voices/references/check", response_model=ReferenceCheck)
async def check(request: Request, owner_id: str, start_ms: int | None = Query(default=None, ge=0)):
    del owner_id
    return await _voices(request).check(await request.body(), start_ms)


@router.post("/owner-voices/clones", response_model=OwnerVoiceInternal)
async def clone(request: Request, owner_id: str, body: VoiceCloneRequest):
    voice = await _voices(request).clone(owner_id, operation_id=body.operation_id, name=body.name,
                                         consent=body.consent, reference_wav_base64=body.reference_wav_base64)
    return (await _with_status(request, [voice]))[0]


@router.get("/owner-voices/{voice_asset_id}", response_model=OwnerVoiceInternal)
async def owner_voice(request: Request, voice_asset_id: str, owner_id: str):
    return (await _with_status(request, [await _voices(request).owner_voice(owner_id, voice_asset_id)]))[0]


@router.patch("/owner-voices/{voice_asset_id}", response_model=OwnerVoiceInternal)
async def update(request: Request, voice_asset_id: str, owner_id: str, body: OwnerVoiceUpdate):
    voices = _voices(request)
    if body.keep:
        voice = await voices.keep(owner_id, voice_asset_id, expected_revision=body.expected_revision,
                                  name=body.name)
    else:
        voice = await voices.update(owner_id, voice_asset_id, body)
    return (await _with_status(request, [voice]))[0]


@router.delete("/owner-voices/{voice_asset_id}", status_code=204)
async def delete(request: Request, voice_asset_id: str, owner_id: str):
    await _voices(request).delete(owner_id, voice_asset_id)
    return Response(status_code=204)
