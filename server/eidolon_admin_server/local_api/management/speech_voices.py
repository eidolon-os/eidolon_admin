"""Voices on the phone: a Companion's voice, the Host's voices, and the Owner's own voices.

What a phone may see is decided here. Provider names and provider voice ids
stay behind this line (V8); a voice is an opaque ``voice_key``. The only
provider word that crosses is ``processor``, the name the clone disclosure
must state.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from urllib.parse import quote

from eidolon_sdk.biz.speech_voice import (
    DESIGN_PROMPT_MAX_CHARS,
    DRAFT_LIMIT,
    OWNER_VOICE_LIMIT,
    PREVIEW_TEXT_MAX_CHARS,
    PREVIEW_TEXT_MIN_CHARS,
    RECORDING_MAX_MS,
    RECORDING_MIN_MS,
    REFERENCE_WINDOW_MS,
    UPLOAD_MAX_BYTES,
    UPLOAD_MAX_MS,
    VoiceRef,
    catalog_voice_key,
    owner_voice_key,
)
from fastapi import Header, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from .backend import ManagementBackendError


class _View(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class VoiceOptionView(_View):
    voice_key: str
    display_name: str
    summary: str | None = None
    gender: str
    age_band: str
    style_labels: list[str]
    is_host_default: bool
    selectable: bool


class VoiceEnrollmentView(_View):
    design: bool
    clone: bool
    #: Who processes a clone recording, named in the disclosure.
    processor: str | None = None


class VoiceLimitsView(_View):
    owner_voices: int = OWNER_VOICE_LIMIT
    drafts: int = DRAFT_LIMIT
    prompt_max_chars: int = DESIGN_PROMPT_MAX_CHARS
    preview_text_min_chars: int = PREVIEW_TEXT_MIN_CHARS
    preview_text_max_chars: int = PREVIEW_TEXT_MAX_CHARS
    recording_min_ms: int = RECORDING_MIN_MS
    recording_max_ms: int = RECORDING_MAX_MS
    upload_max_bytes: int = UPLOAD_MAX_BYTES
    upload_max_ms: int = UPLOAD_MAX_MS
    window_ms: int = REFERENCE_WINDOW_MS


class SpeechVoiceCatalogView(_View):
    catalog_revision: str
    state: Literal["fresh", "stale", "degraded"]
    host_selectable: bool
    not_selectable_reason: str | None = None
    voices: list[VoiceOptionView]
    enrollment: VoiceEnrollmentView
    limits: VoiceLimitsView


class EffectiveVoiceView(_View):
    voice_key: str | None = None
    display_name: str
    #: Why this voice: ``selected``, ``mapped``, ``owner_voice_pending`` … (§5.2, §17.8).
    reason: str


class CompanionSpeechVoiceView(_View):
    companion_id: str
    revision: int
    selected_voice_key: str | None = None
    selected_name: str | None = None
    record_state: Literal["valid", "invalid"]
    #: ``None`` when the Host could not confirm right now.
    effective: EffectiveVoiceView | None = None


class SpeechVoiceChoice(_View):
    #: Absent (or null) puts the Companion back on this Host's voice.
    voice_key: str | None = None
    expected_revision: int = Field(ge=0)


class VoiceConsentView(_View):
    method: Literal["spoken", "attested"]
    subject: Literal["self", "other"]
    subject_label: str | None = None
    accepted_at: datetime


class OwnerVoiceView(_View):
    voice_key: str
    voice_asset_id: str
    name: str
    method: Literal["design", "clone"]
    state: Literal["draft", "active"]
    revision: int
    #: On this Host's current voice model.
    status: Literal["ready", "preparing", "rejected", "unavailable"]
    status_detail: str | None = None
    used_by: list[str]
    has_reference: bool
    design_prompt: str | None = None
    consent: VoiceConsentView | None = None
    created_at: datetime
    draft_expires_at: datetime | None = None


class OwnerVoicesView(_View):
    voices: list[OwnerVoiceView]


class VoiceDesignBody(_View):
    operation_id: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    name: str = Field(min_length=1, max_length=64)
    prompt: str = Field(min_length=1, max_length=DESIGN_PROMPT_MAX_CHARS)
    preview_text: str = Field(min_length=PREVIEW_TEXT_MIN_CHARS, max_length=PREVIEW_TEXT_MAX_CHARS)


class VoiceConsentBody(_View):
    method: Literal["spoken", "attested"]
    subject: Literal["self", "other"]
    subject_label: str | None = Field(default=None, max_length=32)
    consent_version: str = Field(min_length=1, max_length=64)
    statement: str | None = Field(default=None, max_length=200)
    allow_re_enrollment: bool = True


class VoiceCloneBody(_View):
    operation_id: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    name: str = Field(min_length=1, max_length=64)
    consent: VoiceConsentBody
    #: The window ``references/check`` returned, as WAV.
    audio_base64: str


class OwnerVoiceChange(_View):
    expected_revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=64)
    keep: bool = False
    drop_reference: bool = False


class ReferenceCheckView(_View):
    accepted: bool
    problems: list[str]
    duration_ms: int
    window_start_ms: int
    window_ms: int
    speech_ms: int | None = None
    #: The window that would be used, as 24 kHz mono WAV, to play back and to submit.
    audio_base64: str | None = None


# ----------------------------------------------------------------------------- projections

def _catalog_view(raw: dict) -> SpeechVoiceCatalogView:
    return SpeechVoiceCatalogView(
        catalog_revision=raw["catalog_revision"], state=raw["state"], host_selectable=raw["host_selectable"],
        not_selectable_reason=raw.get("not_selectable_reason"),
        voices=[VoiceOptionView(
            voice_key=v["voice_key"], display_name=v["display_name"], summary=v.get("summary"),
            gender=v["gender"], age_band=v["age_band"], style_labels=v["style_labels"],
            is_host_default=v["is_host_default"], selectable=v["selectable"],
        ) for v in raw["voices"]],
        enrollment=VoiceEnrollmentView(**raw.get("enrollment", {})), limits=VoiceLimitsView(),
    )


def _companion_view(raw: dict) -> CompanionSpeechVoiceView:
    record, effective = raw["record"], raw.get("effective")
    selection = record.get("selection")
    key = None
    if selection is not None:
        key = (owner_voice_key(selection["voice_asset_id"]) if selection["source"] == "owner_voice"
               else catalog_voice_key(VoiceRef(provider=selection["provider"], model=selection["model"],
                                               voice_id=selection["voice_id"])))
    return CompanionSpeechVoiceView(
        companion_id=record["companion_id"], revision=record["revision"], selected_voice_key=key,
        selected_name=selection["display_name"] if selection else None, record_state=record["record_state"],
        effective=EffectiveVoiceView(voice_key=effective.get("voice_key"), display_name=effective["display_name"],
                                     reason=effective["reason"]) if effective else None,
    )


def _owner_voice_view(raw: dict) -> OwnerVoiceView:
    voice, consent = raw["voice"], raw["voice"].get("consent")
    return OwnerVoiceView(
        voice_key=owner_voice_key(voice["voice_asset_id"]), voice_asset_id=voice["voice_asset_id"],
        name=voice["name"], method=voice["method"], state=voice["state"], revision=voice["revision"],
        status=raw["status"], status_detail=raw.get("status_detail"), used_by=voice.get("used_by", []),
        has_reference=voice.get("has_reference", False), design_prompt=voice.get("design_prompt"),
        consent=VoiceConsentView(method=consent["method"], subject=consent["subject"],
                                 subject_label=consent.get("subject_label"),
                                 accepted_at=consent["accepted_at"]) if consent else None,
        created_at=voice["created_at"], draft_expires_at=voice.get("draft_expires_at"),
    )


def _check_view(raw: dict) -> ReferenceCheckView:
    return ReferenceCheckView(
        accepted=raw["accepted"], problems=raw["problems"], duration_ms=raw["duration_ms"],
        window_start_ms=raw["window_start_ms"], window_ms=raw["window_ms"], speech_ms=raw.get("speech_ms"),
        audio_base64=raw.get("wav_base64"),
    )


# ----------------------------------------------------------------------------- routes

def register_speech_voice_routes(router, backend, authenticated_owner, refused):
    async def call(authorization, method, path, payload=None, **kwargs) -> dict:
        owner = await authenticated_owner(authorization)
        try:
            return await backend.speech_voice_call(owner_id=owner, method=method, path=path,
                                                   payload=payload, **kwargs)
        except ManagementBackendError as exc:
            raise refused(exc) from exc

    @router.get("/speech-voices", response_model=SpeechVoiceCatalogView,
                responses={304: {"description": "The catalog the caller holds is current."}})
    async def catalog(response: Response, authorization: str | None = Header(default=None),
                      if_none_match: str | None = Header(default=None, alias="If-None-Match")):
        owner = await authenticated_owner(authorization)
        try:
            status, raw, etag = await backend.speech_voice_catalog(owner_id=owner, known_etag=if_none_match)
        except ManagementBackendError as exc:
            raise refused(exc) from exc
        if status == 304:
            return Response(status_code=304, headers={"ETag": etag or ""})
        if etag:
            response.headers["ETag"] = etag
        return _catalog_view(raw)

    @router.post("/speech-voices/refresh", response_model=SpeechVoiceCatalogView)
    async def refresh(authorization: str | None = Header(default=None)):
        return _catalog_view(await call(authorization, "POST", "/speech-voices/refresh"))

    @router.get("/speech-voices/{voice_key}/preview", response_class=Response,
                responses={200: {"content": {"audio/wav": {}}}})
    async def preview(voice_key: str, sample: int = Query(default=1, ge=1, le=2),
                      authorization: str | None = Header(default=None)):
        owner = await authenticated_owner(authorization)
        try:
            data = await backend.speech_voice_preview(owner_id=owner, voice_key=voice_key, sample=sample)
        except ManagementBackendError as exc:
            raise refused(exc) from exc
        return Response(content=data, media_type="audio/wav", headers={"Cache-Control": "private, max-age=86400"})

    @router.get("/companions/{companion_id}/speech-voice", response_model=CompanionSpeechVoiceView)
    async def companion_voice(companion_id: str, authorization: str | None = Header(default=None)):
        return _companion_view(await call(
            authorization, "GET", f"/companions/{quote(companion_id, safe='')}/speech-voice"))

    @router.put("/companions/{companion_id}/speech-voice", response_model=CompanionSpeechVoiceView)
    async def choose(companion_id: str, body: SpeechVoiceChoice,
                     authorization: str | None = Header(default=None)):
        return _companion_view(await call(
            authorization, "PUT", f"/companions/{quote(companion_id, safe='')}/speech-voice",
            body.model_dump(mode="json")))

    @router.get("/owner-voices", response_model=OwnerVoicesView)
    async def owner_voices(authorization: str | None = Header(default=None)):
        raw = await call(authorization, "GET", "/owner-voices")
        return OwnerVoicesView(voices=[_owner_voice_view(v) for v in raw["voices"]])

    @router.post("/owner-voices/designs", response_model=OwnerVoiceView)
    async def design(body: VoiceDesignBody, authorization: str | None = Header(default=None)):
        return _owner_voice_view(await call(authorization, "POST", "/owner-voices/designs",
                                            body.model_dump(mode="json")))

    @router.post("/owner-voices/references/check", response_model=ReferenceCheckView,
                 openapi_extra={"requestBody": {"required": True, "content": {
                     "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}}})
    async def check(request: Request, start_ms: int | None = Query(default=None, ge=0),
                    authorization: str | None = Header(default=None)):
        audio = await request.body()
        return _check_view(await call(
            authorization, "POST", "/owner-voices/references/check",
            params={"start_ms": str(start_ms)} if start_ms is not None else None,
            content=audio, content_type="application/octet-stream"))

    @router.post("/owner-voices/clones", response_model=OwnerVoiceView)
    async def clone(body: VoiceCloneBody, authorization: str | None = Header(default=None)):
        consent = body.consent.model_dump(mode="json")
        consent["accepted_at"] = datetime.now().astimezone().isoformat()
        return _owner_voice_view(await call(authorization, "POST", "/owner-voices/clones", {
            "operation_id": body.operation_id, "name": body.name, "consent": consent,
            "reference_wav_base64": body.audio_base64}))

    @router.get("/owner-voices/{voice_asset_id}", response_model=OwnerVoiceView)
    async def owner_voice(voice_asset_id: str, authorization: str | None = Header(default=None)):
        return _owner_voice_view(await call(authorization, "GET", f"/owner-voices/{quote(voice_asset_id, safe='')}"))

    @router.patch("/owner-voices/{voice_asset_id}", response_model=OwnerVoiceView)
    async def change(voice_asset_id: str, body: OwnerVoiceChange,
                     authorization: str | None = Header(default=None)):
        return _owner_voice_view(await call(authorization, "PATCH", f"/owner-voices/{quote(voice_asset_id, safe='')}",
                                            body.model_dump(mode="json", exclude_none=True)))

    @router.delete("/owner-voices/{voice_asset_id}", status_code=204)
    async def delete(voice_asset_id: str, authorization: str | None = Header(default=None)):
        await call(authorization, "DELETE", f"/owner-voices/{quote(voice_asset_id, safe='')}")
        return Response(status_code=204)
