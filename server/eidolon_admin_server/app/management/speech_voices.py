"""Speech voices as an Owner manages them: a Companion's voice, the Host's catalog, and the Owner's own voices.

Admin only composes. Data stores the choices and the Owner's voices;
channel-provider knows what this Host can speak and makes new voices. Ownership
is proven through Data before anything Owner-specific reaches Channel, and
every refusal travels as an ``AuthorityFailure`` to the one handler that turns
refusals into responses.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import httpx
from eidolon_sdk.biz.speech_voice import (
    CatalogVoiceSelection,
    CompanionSpeechVoice,
    EnrolledVoice,
    HostVoiceCatalog,
    OwnerVoice,
    OwnerVoiceBinding,
    OwnerVoiceConsent,
    OwnerVoiceCreate,
    OwnerVoiceKey,
    OwnerVoiceSelection,
    OwnerVoiceUpdate,
    ReferenceCheck,
    ResolvedSpeechVoice,
    SpeechVoiceUpdate,
    VoiceRef,
    decode_voice_key,
)
from pydantic import TypeAdapter, ValidationError

from eidolon_admin_server.app.control_plane.errors import AuthorityFailure
from eidolon_admin_server.app.gateway.proxy import upstream_auth_headers

logger = logging.getLogger(__name__)

#: A binding the provider is still deciding on is asked about again after this long.
STATUS_RECHECK = timedelta(seconds=3)


class ChannelVoices:
    """channel-provider's voice routes as one client."""

    def __init__(self, registry, http: httpx.AsyncClient) -> None:
        self._registry, self._http = registry, http

    @classmethod
    def of(cls, app) -> ChannelVoices:
        return cls(app.state.registry, app.state.http_client)

    async def _send(self, method: str, path: str, *, timeout: float = 10, **kwargs) -> httpx.Response:
        service = self._registry.get("channel-provider")
        if service is None or not service.base_url:
            raise AuthorityFailure("channel", "configuration", "主机的语音服务没有配置", 503)
        url = service.base_url.rstrip("/") + (service.upstream_prefix or "").rstrip("/") + path
        headers = {**upstream_auth_headers(service), **kwargs.pop("headers", {})}
        try:
            response = await self._http.request(method, url, headers=headers, timeout=timeout, **kwargs)
        except httpx.HTTPError as exc:
            raise AuthorityFailure("channel", "unavailable", "主机的语音服务暂时连不上", 503,
                                   retryable=True) from exc
        if response.status_code in (200, 204, 304):
            return response
        try:
            problem = response.json()
        except ValueError:
            problem = {}
        code, detail = problem.get("code"), problem.get("detail") or "语音服务拒绝了这个请求"
        if response.status_code == 409:
            raise AuthorityFailure("channel", "conflict", detail, 409, 409, code=code)
        if response.status_code in (413, 422):
            raise AuthorityFailure("channel", "invalid_request", detail, 422, response.status_code, code=code)
        if response.status_code == 429:
            raise AuthorityFailure("channel", "unavailable", "试听正忙，请稍后再试", 503, 429, True, code)
        raise AuthorityFailure("channel", "upstream_failure", detail, 502, response.status_code, True, code)

    @staticmethod
    def _model(response: httpx.Response, model):
        try:
            return TypeAdapter(model).validate_json(response.content)
        except (ValueError, ValidationError) as exc:
            raise AuthorityFailure("channel", "contract_violation",
                                   "语音服务的回答不符合约定", 502) from exc

    async def catalog(self, etag: str | None = None) -> tuple[HostVoiceCatalog | None, str | None]:
        """The catalog and its ETag; ``None`` when ``etag`` is still current."""
        response = await self._send("GET", "/v1/speech-voices",
                                    headers={"If-None-Match": etag} if etag else {})
        if response.status_code == 304:
            return None, etag
        return self._model(response, HostVoiceCatalog), response.headers.get("ETag")

    async def refresh(self) -> HostVoiceCatalog:
        return self._model(await self._send("POST", "/v1/speech-voices/refresh"), HostVoiceCatalog)

    async def resolve(self, record: CompanionSpeechVoice | None) -> ResolvedSpeechVoice:
        body = {"speech_voice": record.model_dump(mode="json") if record else None}
        return self._model(await self._send("POST", "/v1/speech-voices/resolve", json=body), ResolvedSpeechVoice)

    async def preview(self, ref: VoiceRef, sample: int) -> bytes:
        response = await self._send("POST", "/v1/speech-voices/previews", timeout=75,
                                    json={"ref": ref.model_dump(), "sample": sample})
        return response.content

    async def check(self, audio: bytes, start_ms: int | None) -> ReferenceCheck:
        params = {"start_ms": str(start_ms)} if start_ms is not None else None
        response = await self._send("POST", "/v1/speech-voices/references/check", content=audio,
                                    params=params, timeout=30,
                                    headers={"Content-Type": "application/octet-stream"})
        return self._model(response, ReferenceCheck)

    async def design(self, prompt: str, preview_text: str) -> EnrolledVoice:
        response = await self._send("POST", "/v1/speech-voices/enrollments/design", timeout=75,
                                    json={"prompt": prompt, "preview_text": preview_text})
        return self._model(response, EnrolledVoice)

    async def clone(self, reference_wav_base64: str) -> EnrolledVoice:
        response = await self._send("POST", "/v1/speech-voices/enrollments/clone", timeout=75,
                                    json={"reference_wav_base64": reference_wav_base64})
        return self._model(response, EnrolledVoice)

    async def status(self, provider: str, voice_id: str) -> OwnerVoiceBinding:
        response = await self._send("GET", f"/v1/speech-voices/enrollments/{quote(voice_id, safe='')}",
                                    params={"provider": provider})
        return self._model(response, OwnerVoiceBinding)

    async def delete(self, provider: str, voice_id: str) -> None:
        await self._send("DELETE", f"/v1/speech-voices/enrollments/{quote(voice_id, safe='')}",
                         params={"provider": provider}, timeout=30)

    async def list_own(self, provider: str) -> list[str]:
        response = await self._send("GET", "/v1/speech-voices/enrollments", params={"provider": provider},
                                    timeout=30)
        return self._model(response, list[str])


def host_model(catalog: HostVoiceCatalog) -> VoiceRef | None:
    """(provider, model) this Host speaks now, as its default voice."""
    return next((v.ref for v in catalog.voices if v.is_host_default), None)


@dataclass(frozen=True)
class CompanionVoice:
    """A Companion's choice next to what a session would actually use now."""

    record: CompanionSpeechVoice
    effective: ResolvedSpeechVoice | None


class SpeechVoices:
    """The Owner-facing operations. ``data`` is the Data authority client; ``roster`` proves ownership."""

    def __init__(self, *, data, roster, channel: ChannelVoices) -> None:
        self._data, self._roster, self._channel = data, roster, channel

    # ------------------------------------------------------------------ a Companion's voice

    async def companion_voice(self, owner_id: str, companion_id: str) -> CompanionVoice:
        await self._roster.get_owner_companion(owner_id, companion_id)
        record = await self._data.speech_voice_call(
            "GET", f"/companions/{quote(companion_id, safe='')}/speech-voice", CompanionSpeechVoice)
        try:
            effective = await self._channel.resolve(record)
        except AuthorityFailure as exc:
            if exc.authority != "channel":
                raise
            effective = None  # the page says the Host cannot confirm right now
        return CompanionVoice(record, effective)

    async def choose(self, owner_id: str, companion_id: str, voice_key: str | None,
                     expected_revision: int) -> CompanionVoice:
        await self._roster.get_owner_companion(owner_id, companion_id)
        selection = None
        if voice_key is not None:
            selection = await self._selection(owner_id, voice_key)
        await self._data.speech_voice_call(
            "PUT", f"/companions/{quote(companion_id, safe='')}/speech-voice", CompanionSpeechVoice,
            json=SpeechVoiceUpdate(expected_revision=expected_revision, selection=selection)
            .model_dump(mode="json"))
        return await self.companion_voice(owner_id, companion_id)

    async def _selection(self, owner_id: str, voice_key: str):
        try:
            target = decode_voice_key(voice_key)
        except ValueError as exc:
            raise AuthorityFailure("channel", "invalid_request", "不认识这个声音", 422) from exc
        if isinstance(target, OwnerVoiceKey):
            voice = await self.owner_voice(owner_id, target.voice_asset_id)
            if voice.state != "active":
                raise AuthorityFailure("data", "conflict", "这个声音还没有保存", 409, code="owner_voice_draft")
            return OwnerVoiceSelection(voice_asset_id=voice.voice_asset_id, display_name=voice.name)
        catalog, _ = await self._channel.catalog()
        option = catalog.option(voice_key) if catalog else None
        if option is None or not option.selectable:
            raise AuthorityFailure("channel", "conflict", "这台主机现在不能用这个声音", 409,
                                   code="voice_not_selectable")
        ref = option.ref
        return CatalogVoiceSelection(provider=ref.provider, model=ref.model, voice_id=ref.voice_id,
                                     display_name=option.display_name)

    async def preview(self, owner_id: str, voice_key: str, sample: int) -> bytes:
        try:
            target = decode_voice_key(voice_key)
        except ValueError as exc:
            raise AuthorityFailure("channel", "invalid_request", "不认识这个声音", 422) from exc
        catalog, _ = await self._channel.catalog()
        if isinstance(target, OwnerVoiceKey):
            voice = await self.owner_voice(owner_id, target.voice_asset_id)
            current = host_model(catalog) if catalog else None
            binding = voice.binding_for(current.provider, current.model) if current else None
            if voice.state == "active" and binding is not None and binding.status == "ready":
                return await self._channel.preview(VoiceRef(
                    provider=binding.provider, model=binding.model, voice_id=binding.provider_voice_id), sample)
            if voice.has_preview:
                return await self._data.speech_voice_call("GET", self._voice_path(owner_id, voice.voice_asset_id)
                                                          + "/preview", bytes)
            raise AuthorityFailure("channel", "not_found", "这个声音在这台主机上还不能试听", 404)
        option = catalog.option(voice_key) if catalog else None
        if option is None:
            raise AuthorityFailure("channel", "not_found", "这台主机没有这个声音", 404)
        return await self._channel.preview(option.ref, sample)

    # ------------------------------------------------------------------ the Owner's own voices

    @staticmethod
    def _voice_path(owner_id: str, voice_asset_id: str | None = None) -> str:
        path = f"/owners/{quote(owner_id, safe='')}/voices"
        return path + (f"/{quote(voice_asset_id, safe='')}" if voice_asset_id else "")

    async def owner_voices(self, owner_id: str) -> list[OwnerVoice]:
        voices = await self._data.speech_voice_call("GET", self._voice_path(owner_id), list[OwnerVoice])
        return [await self._fresh(voice) for voice in voices]

    async def owner_voice(self, owner_id: str, voice_asset_id: str) -> OwnerVoice:
        voice = await self._data.speech_voice_call("GET", self._voice_path(owner_id, voice_asset_id), OwnerVoice)
        return await self._fresh(voice)

    async def _fresh(self, voice: OwnerVoice) -> OwnerVoice:
        """Ask the provider about a binding it is still reviewing, at most every few seconds."""
        now = datetime.now(UTC)
        for binding in voice.bindings:
            checked = binding.last_checked_at or binding.created_at
            if binding.status == "deploying" and (checked is None or now - checked >= STATUS_RECHECK):
                voice = await self.record_status(voice, binding)
        return voice

    async def record_status(self, voice: OwnerVoice, binding: OwnerVoiceBinding) -> OwnerVoice:
        try:
            current = await self._channel.status(binding.provider, binding.provider_voice_id)
        except AuthorityFailure:
            return voice  # asked again next time
        return await self._data.speech_voice_call(
            "PUT", self._voice_path(voice.owner_id, voice.voice_asset_id) + "/binding", OwnerVoice,
            json=current.model_dump(mode="json"))

    async def _existing(self, owner_id: str, operation_id: str) -> OwnerVoice | None:
        voices = await self._data.speech_voice_call("GET", self._voice_path(owner_id), list[OwnerVoice])
        return next((v for v in voices if v.operation_id == operation_id), None)

    async def _store(self, owner_id: str, request: OwnerVoiceCreate) -> OwnerVoice:
        """Data keeps the new voice; if it will not, the provider voice must not be left behind."""
        try:
            return await self._data.speech_voice_call("POST", self._voice_path(owner_id), OwnerVoice,
                                                      json=request.model_dump(mode="json"))
        except AuthorityFailure:
            try:
                await self._channel.delete(request.binding.provider, request.binding.provider_voice_id)
            except AuthorityFailure:
                logger.warning("provider voice %s left for the orphan sweep", request.binding.provider_voice_id)
            raise

    async def design(self, owner_id: str, *, operation_id: str, name: str, prompt: str,
                     preview_text: str) -> OwnerVoice:
        if (existing := await self._existing(owner_id, operation_id)) is not None:
            return existing
        made = await self._channel.design(prompt, preview_text)
        return await self._store(owner_id, OwnerVoiceCreate(
            operation_id=operation_id, name=name, method="design", state="draft", design_prompt=prompt,
            design_preview_text=preview_text, preview_wav_base64=made.preview_wav_base64, binding=made.binding))

    async def keep(self, owner_id: str, voice_asset_id: str, *, expected_revision: int,
                   name: str | None) -> OwnerVoice:
        voice = await self._update(owner_id, voice_asset_id, OwnerVoiceUpdate(
            expected_revision=expected_revision, name=name, keep=True))
        # A designed voice reads a fixed passage now, so it can be cloned again on another model.
        binding = next((b for b in voice.bindings if b.status == "ready"), None)
        if voice.method == "design" and not voice.has_reference and binding is not None:
            try:
                rendition = await self._channel.preview(VoiceRef(
                    provider=binding.provider, model=binding.model, voice_id=binding.provider_voice_id), 0)
                voice = await self._update(owner_id, voice_asset_id, OwnerVoiceUpdate(
                    expected_revision=voice.revision,
                    reference_wav_base64=base64.b64encode(rendition).decode()))
            except AuthorityFailure:
                logger.warning("rendition of %s not made; it cannot follow a model change", voice_asset_id)
        return voice

    async def check(self, audio: bytes, start_ms: int | None) -> ReferenceCheck:
        return await self._channel.check(audio, start_ms)

    async def clone(self, owner_id: str, *, operation_id: str, name: str, consent: OwnerVoiceConsent,
                    reference_wav_base64: str) -> OwnerVoice:
        if (existing := await self._existing(owner_id, operation_id)) is not None:
            return existing
        # Judged again here: what the phone sent is not trusted to be what it checked.
        checked = await self._channel.check(base64.b64decode(reference_wav_base64, validate=True), None)
        if not checked.accepted or checked.wav_base64 is None:
            raise AuthorityFailure("channel", "invalid_request", "录音不符合要求，请重新录制", 422,
                                   code="reference_rejected")
        made = await self._channel.clone(checked.wav_base64)
        return await self._store(owner_id, OwnerVoiceCreate(
            operation_id=operation_id, name=name, method="clone", state="active", consent=consent,
            reference_wav_base64=checked.wav_base64, binding=made.binding))

    async def _update(self, owner_id: str, voice_asset_id: str, update: OwnerVoiceUpdate) -> OwnerVoice:
        return await self._data.speech_voice_call(
            "PATCH", self._voice_path(owner_id, voice_asset_id), OwnerVoice, json=update.model_dump(mode="json"))

    async def update(self, owner_id: str, voice_asset_id: str, update: OwnerVoiceUpdate) -> OwnerVoice:
        return await self._update(owner_id, voice_asset_id, update)

    async def delete(self, owner_id: str, voice_asset_id: str) -> None:
        await self._data.speech_voice_call("DELETE", self._voice_path(owner_id, voice_asset_id))
