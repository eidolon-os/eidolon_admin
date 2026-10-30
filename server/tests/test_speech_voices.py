"""Speech voices through Admin: ownership, catalog checks, making voices, upkeep, and what a phone sees."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from eidolon_admin_server.app.control_plane.errors import AuthorityFailure
from eidolon_admin_server.app.management.owner_voice_reconciler import (
    OwnerVoiceReconciler,
)
from eidolon_admin_server.app.management.speech_voices import (
    ChannelVoices,
    SpeechVoices,
)
from eidolon_sdk.biz.speech_voice import (
    CompanionSpeechVoice,
    HostVoiceCatalog,
    OwnerVoice,
    OwnerVoiceBinding,
    OwnerVoiceConsent,
    ProviderVoiceTombstone,
    VoiceOption,
    VoiceRef,
    catalog_voice_key,
    owner_voice_key,
)

NOW = datetime(2026, 10, 31, tzinfo=UTC)
DEFAULT = VoiceRef(provider="bailian", model="qwen-audio-3.1-tts-flash", voice_id="longanyang_v3.1")
OTHER = VoiceRef(provider="bailian", model="qwen-audio-3.1-tts-flash", voice_id="longwan_v3.1")
WAV = base64.b64encode(b"RIFF\x00\x00\x00\x00WAVEfmt ").decode()


def _option(ref: VoiceRef, name: str, *, default=False, selectable=True) -> VoiceOption:
    return VoiceOption(voice_key=catalog_voice_key(ref), ref=ref, display_name=name, gender="female",
                       age_band="youth", languages=["zh-CN"], styles=[], style_labels=[], rank=1,
                       is_host_default=default, selectable=selectable)


CATALOG = HostVoiceCatalog(
    catalog_revision="sha256:abc", state="fresh", host_selectable=True,
    voices=[_option(DEFAULT, "龙安洋", default=True), _option(OTHER, "龙婉")],
    enrollment={"design": True, "clone": True, "processor": "阿里云百炼"},
)


class Data:
    """Data's voice routes in memory, owned by ``owner-1`` with one Companion."""

    def __init__(self) -> None:
        self.record = CompanionSpeechVoice(companion_id="c1", revision=0)
        self.voices: dict[str, OwnerVoice] = {}
        self.tombstones: list[ProviderVoiceTombstone] = []
        self.calls: list[tuple[str, str]] = []
        self.refuse_create = False

    async def get_owner_companion(self, owner_id, companion_id):
        if (owner_id, companion_id) != ("owner-1", "c1"):
            raise AuthorityFailure("data", "not_found", "companion not found", 404)
        return SimpleNamespace(companion_id=companion_id)

    async def speech_voice_call(self, method, path, model=None, *, json=None, params=None):
        self.calls.append((method, path))
        if path == "/companions/c1/speech-voice":
            if method == "PUT":
                if json["expected_revision"] != self.record.revision:
                    raise AuthorityFailure("data", "conflict", "changed", 409, code="speech_voice_changed")
                self.record = CompanionSpeechVoice(companion_id="c1", revision=self.record.revision + 1,
                                                   selection=json["selection"])
            return self.record
        if path == "/owners/owner-1/voices" and method == "GET":
            return list(self.voices.values())
        if path == "/owners/owner-1/voices" and method == "POST":
            if self.refuse_create:
                raise AuthorityFailure("data", "conflict", "too many", 409, code="owner_voice_limit")
            voice = OwnerVoice(
                voice_asset_id=f"ov{len(self.voices) + 1}", owner_id="owner-1", name=json["name"],
                method=json["method"], state=json["state"], revision=1, operation_id=json["operation_id"],
                consent=json.get("consent"), has_reference=bool(json.get("reference_wav_base64")),
                has_preview=bool(json.get("preview_wav_base64")), bindings=[json["binding"]],
                created_at=NOW, updated_at=NOW)
            self.voices[voice.voice_asset_id] = voice
            return voice
        if path.startswith("/owners/owner-1/voices/"):
            voice_id, _, tail = path.removeprefix("/owners/owner-1/voices/").partition("/")
            voice = self.voices[voice_id]
            if tail == "binding":
                kept = [b for b in voice.bindings if (b.provider, b.model) != (json["provider"], json["model"])]
                voice = voice.model_copy(update={"bindings": [*kept, OwnerVoiceBinding(**json)]})
            elif tail == "preview":
                return b"RIFF-design-preview"
            elif tail == "reference":
                return b"RIFF-reference"
            elif method == "PATCH":
                update = {"revision": voice.revision + 1}
                if json.get("keep"):
                    update["state"] = "active"
                if json.get("name"):
                    update["name"] = json["name"]
                if json.get("reference_wav_base64"):
                    update["has_reference"] = True
                voice = voice.model_copy(update=update)
            elif method == "DELETE":
                for b in self.voices.pop(voice_id).bindings:
                    self.tombstones.append(ProviderVoiceTombstone(
                        provider=b.provider, model=b.model, provider_voice_id=b.provider_voice_id,
                        reason="deleted", created_at=NOW))
                return None
            self.voices[voice_id] = voice
            return voice
        if path == "/owner-voices/pending":
            return [v for v in self.voices.values() if any(b.status == "deploying" for b in v.bindings)]
        if path == "/owner-voices/needing-binding":
            return [v for v in self.voices.values() if v.binding_for(params["provider"], params["model"]) is None]
        if path == "/owner-voices/expire-drafts":
            return {"expired": 0}
        if path == "/provider-voice-tombstones":
            return self.tombstones
        if path.startswith("/provider-voice-tombstones/"):
            voice_id = path.rsplit("/", 1)[1]
            self.tombstones = [t for t in self.tombstones if t.provider_voice_id != voice_id]
            return None
        if path == "/provider-voices/bailian":
            ids = [b.provider_voice_id for v in self.voices.values() for b in v.bindings]
            return ids + [t.provider_voice_id for t in self.tombstones]
        raise AssertionError(f"unexpected Data call {method} {path}")


class Channel:
    """channel-provider's voice routes behind a mock transport."""

    def __init__(self) -> None:
        self.statuses: dict[str, str] = {}
        self.deleted: list[str] = []
        self.own: list[str] = []
        self.check_accepts = True
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/v1/speech-voices":
            if request.headers.get("If-None-Match") == '"sha256:abc"':
                return httpx.Response(304)
            return httpx.Response(200, json=CATALOG.model_dump(mode="json"), headers={"ETag": '"sha256:abc"'})
        if path == "/v1/speech-voices/resolve":
            record = json.loads(request.content)["speech_voice"]
            chosen = (record or {}).get("selection") or {}
            return httpx.Response(200, json={"ref": DEFAULT.model_dump(), "voice_key": "k",
                                             "display_name": chosen.get("display_name", "龙安洋"),
                                             "reason": "selected" if chosen else "unset"})
        if path == "/v1/speech-voices/previews":
            return httpx.Response(200, content=b"RIFF-preview:" + request.content, headers={"Content-Type": "audio/wav"})
        if path == "/v1/speech-voices/references/check":
            return httpx.Response(200, json={
                "accepted": self.check_accepts, "problems": [] if self.check_accepts else ["too_short"],
                "duration_ms": 12000, "window_start_ms": 0, "window_ms": 12000, "wav_base64": WAV})
        if path == "/v1/speech-voices/enrollments/design":
            return httpx.Response(200, json={"binding": _binding("m-vd-1", "ready"), "preview_wav_base64": WAV})
        if path == "/v1/speech-voices/enrollments/clone":
            return httpx.Response(200, json={"binding": _binding("m-clone-1", "deploying")})
        if path.startswith("/v1/speech-voices/enrollments/"):
            voice_id = path.rsplit("/", 1)[1]
            if request.method == "DELETE":
                self.deleted.append(voice_id)
                return httpx.Response(204)
            return httpx.Response(200, json=_binding(voice_id, self.statuses.get(voice_id, "ready")))
        if path == "/v1/speech-voices/enrollments":
            return httpx.Response(200, json=self.own)
        return httpx.Response(404, json={"code": "NOT_FOUND", "detail": path})


def _binding(voice_id: str, status: str) -> dict:
    return {"provider": "bailian", "model": DEFAULT.model, "provider_voice_id": voice_id, "status": status,
            "created_at": NOW.isoformat(), "last_checked_at": "2000-01-01T00:00:00Z"}


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setenv("SPEECH_VOICE_TEST_TOKEN", "provider-token")
    data, channel = Data(), Channel()
    registry = {"channel-provider": SimpleNamespace(
        base_url="http://channel", upstream_prefix="",
        auth=SimpleNamespace(type="bearer", token_env="SPEECH_VOICE_TEST_TOKEN"))}
    http = httpx.AsyncClient(transport=httpx.MockTransport(channel.handle))
    client = ChannelVoices(registry, http)
    return SimpleNamespace(data=data, channel=channel, client=client,
                           voices=SpeechVoices(data=data, roster=data, channel=client))


# ----------------------------------------------------------------------------- choosing

async def test_choosing_a_host_voice_stores_its_reference_and_resolves(world) -> None:
    result = await world.voices.choose("owner-1", "c1", catalog_voice_key(OTHER), 0)
    stored = world.data.record.selection
    assert (stored.voice_id, stored.display_name) == ("longwan_v3.1", "龙婉")
    assert result.effective.reason == "selected"
    assert world.channel.requests[0].headers["Authorization"] == "Bearer provider-token"


async def test_a_voice_this_host_cannot_speak_is_refused_and_another_owner_is_not_found(world) -> None:
    gone = catalog_voice_key(VoiceRef(provider="bailian", model="cosyvoice-v3-flash", voice_id="x"))
    with pytest.raises(AuthorityFailure) as refused:
        await world.voices.choose("owner-1", "c1", gone, 0)
    assert refused.value.code == "voice_not_selectable"
    with pytest.raises(AuthorityFailure) as stranger:
        await world.voices.choose("owner-2", "c1", None, 0)
    assert stranger.value.status_code == 404
    with pytest.raises(AuthorityFailure) as garbage:
        await world.voices.choose("owner-1", "c1", "nonsense", 0)
    assert garbage.value.status_code == 422


async def test_a_page_still_opens_when_channel_is_down(world, monkeypatch) -> None:
    world.client._registry = {}
    result = await world.voices.companion_voice("owner-1", "c1")
    assert result.effective is None and result.record.revision == 0


# ----------------------------------------------------------------------------- making voices

async def test_designing_keeps_a_draft_with_its_preview_and_keeping_makes_a_rendition(world) -> None:
    draft = await world.voices.design("owner-1", operation_id="op-design-1", name="小禾的声音",
                                      prompt="清亮的女声", preview_text="你好呀，我是你的新伙伴，今天想聊点什么？")
    assert draft.state == "draft" and draft.has_preview
    again = await world.voices.design("owner-1", operation_id="op-design-1", name="x",
                                      prompt="y", preview_text="你好呀，我是你的新伙伴，今天想聊点什么？")
    assert again.voice_asset_id == draft.voice_asset_id
    assert sum(r.url.path.endswith("/design") for r in world.channel.requests) == 1

    # A draft plays the preview the provider generated; a kept voice speaks through Channel.
    assert await world.voices.preview("owner-1", owner_voice_key(draft.voice_asset_id), 1) == b"RIFF-design-preview"
    kept = await world.voices.keep("owner-1", draft.voice_asset_id, expected_revision=1, name=None)
    assert kept.state == "active" and kept.has_reference  # the rendition for a later model change
    spoken = await world.voices.preview("owner-1", owner_voice_key(draft.voice_asset_id), 2)
    assert spoken.startswith(b"RIFF-preview:") and b'"sample":2' in spoken


async def test_cloning_rechecks_the_reference_and_a_refused_store_deletes_the_provider_voice(world) -> None:
    consent = OwnerVoiceConsent(method="spoken", subject="self", consent_version="2026-10-31", accepted_at=NOW)
    world.channel.check_accepts = False
    with pytest.raises(AuthorityFailure) as refused:
        await world.voices.clone("owner-1", operation_id="op-clone-0", name="我", consent=consent,
                                 reference_wav_base64=WAV)
    assert refused.value.code == "reference_rejected"
    world.channel.check_accepts = True
    world.data.refuse_create = True
    with pytest.raises(AuthorityFailure):
        await world.voices.clone("owner-1", operation_id="op-clone-1", name="我", consent=consent,
                                 reference_wav_base64=WAV)
    assert world.channel.deleted == ["m-clone-1"]
    world.data.refuse_create = False
    voice = await world.voices.clone("owner-1", operation_id="op-clone-2", name="我", consent=consent,
                                     reference_wav_base64=WAV)
    assert voice.bindings[0].status == "deploying"


async def test_reading_a_voice_under_review_asks_the_provider(world) -> None:
    consent = OwnerVoiceConsent(method="attested", subject="other", consent_version="v", accepted_at=NOW)
    voice = await world.voices.clone("owner-1", operation_id="op-clone-3", name="妈妈", consent=consent,
                                     reference_wav_base64=WAV)
    world.channel.statuses["m-clone-1"] = "ready"
    fresh = await world.voices.owner_voice("owner-1", voice.voice_asset_id)
    assert fresh.bindings[0].status == "ready"


# ----------------------------------------------------------------------------- upkeep

async def test_the_reconciler_advances_reviews_buries_and_sweeps_orphans_twice_seen(world) -> None:
    consent = OwnerVoiceConsent(method="spoken", subject="self", consent_version="v", accepted_at=NOW)
    voice = await world.voices.clone("owner-1", operation_id="op-clone-4", name="我", consent=consent,
                                     reference_wav_base64=WAV)
    world.channel.statuses["m-clone-1"] = "rejected"
    clock = [0.0]
    reconciler = OwnerVoiceReconciler(data=world.data, channel=world.client, clock=lambda: clock[0])
    world.channel.own = ["m-clone-1", "m-orphan"]
    await reconciler.tick()
    assert world.data.voices[voice.voice_asset_id].bindings[0].status == "rejected"
    assert "m-orphan" not in world.channel.deleted  # seen once: maybe still being created

    await world.voices.delete("owner-1", voice.voice_asset_id)
    clock[0] = 3600
    await reconciler.tick()
    assert set(world.channel.deleted) == {"m-clone-1", "m-orphan"}
    assert world.data.tombstones == []


async def test_an_authority_not_up_yet_is_one_line_and_the_other_steps_still_run(world, caplog) -> None:
    async def down():
        raise AuthorityFailure("data", "unavailable", "System Service Directory is unreachable", 503)

    reconciler = OwnerVoiceReconciler(data=world.data, channel=world.client)
    reconciler.advance = down
    with caplog.at_level("WARNING"):
        await reconciler.tick()
    skipped = [r for r in caplog.records if "skipped" in r.getMessage()]
    assert len(skipped) == 1 and skipped[0].exc_info is None
    assert "unreachable" in skipped[0].getMessage()


async def test_after_a_model_change_used_voices_are_cloned_again_from_their_reference(world) -> None:
    consent = OwnerVoiceConsent(method="spoken", subject="self", consent_version="v", accepted_at=NOW)
    voice = await world.voices.clone("owner-1", operation_id="op-clone-5", name="我", consent=consent,
                                     reference_wav_base64=WAV)
    world.data.voices[voice.voice_asset_id] = voice.model_copy(update={"bindings": [OwnerVoiceBinding(
        provider="bailian", model="cosyvoice-v3-flash", provider_voice_id="old", status="ready")]})
    await OwnerVoiceReconciler(data=world.data, channel=world.client).re_enroll()
    current = world.data.voices[voice.voice_asset_id].binding_for("bailian", DEFAULT.model)
    assert current is not None and current.provider_voice_id == "m-clone-1"


async def test_a_review_that_never_ends_is_marked_failed(world) -> None:
    consent = OwnerVoiceConsent(method="spoken", subject="self", consent_version="v", accepted_at=NOW)
    voice = await world.voices.clone("owner-1", operation_id="op-clone-6", name="我", consent=consent,
                                     reference_wav_base64=WAV)
    old = voice.bindings[0].model_copy(update={"created_at": datetime.now(UTC) - timedelta(minutes=20)})
    world.data.voices[voice.voice_asset_id] = voice.model_copy(update={"bindings": [old]})
    world.channel.statuses["m-clone-1"] = "deploying"
    await OwnerVoiceReconciler(data=world.data, channel=world.client).advance()
    assert world.data.voices[voice.voice_asset_id].bindings[0].status == "failed"


# ----------------------------------------------------------------------------- the phone

from eidolon_admin_server.local_api.management.speech_voices import (
    _catalog_view,
    _companion_view,
    _owner_voice_view,
)


def test_what_a_phone_sees_carries_no_provider_reference() -> None:
    catalog = _catalog_view(CATALOG.model_dump(mode="json")).model_dump(mode="json")
    text = json.dumps(catalog)
    assert "ref" not in catalog["voices"][0] and "longanyang" not in text and "bailian" not in text
    assert catalog["enrollment"]["processor"] == "阿里云百炼"  # the disclosure is the one exception
    assert catalog["limits"]["recording_max_ms"] == 25000

    record = CompanionSpeechVoice(companion_id="c1", revision=2, selection={
        "source": "catalog", "provider": "bailian", "model": DEFAULT.model, "voice_id": "longwan_v3.1",
        "display_name": "龙婉"})
    view = _companion_view({"record": record.model_dump(mode="json"), "effective": None})
    assert view.selected_voice_key == catalog_voice_key(OTHER) and view.effective is None

    voice = OwnerVoice(voice_asset_id="ov1", owner_id="o", name="小禾", method="design", state="active", revision=2,
                       operation_id="op-design-9", bindings=[OwnerVoiceBinding(**_binding("secret-id", "ready"))],
                       created_at=NOW, updated_at=NOW)
    shown = _owner_voice_view({"voice": voice.model_dump(mode="json"), "status": "ready"}).model_dump(mode="json")
    assert shown["voice_key"] == "o.ov1" and "secret-id" not in json.dumps(shown)


class _Backend:
    def __init__(self) -> None:
        self.seen: list[dict] = []

    async def speech_voice_catalog(self, *, owner_id, known_etag):
        assert owner_id == "owner-1"
        if known_etag == '"sha256:abc"':
            return 304, None, known_etag
        return 200, CATALOG.model_dump(mode="json"), '"sha256:abc"'

    async def speech_voice_preview(self, *, owner_id, voice_key, sample):
        return b"RIFF-wav"

    async def speech_voice_call(self, **kwargs):
        self.seen.append(kwargs)
        return {"accepted": True, "problems": [], "duration_ms": 30000, "window_start_ms": 5000,
                "window_ms": 20000, "wav_base64": WAV}

    def __getattr__(self, name):
        async def unused(*args, **kwargs):
            raise AssertionError(f"this test never calls {name}")
        return unused


async def test_the_phone_routes_authenticate_relay_audio_and_uploads(tmp_path, monkeypatch) -> None:
    from tests.test_management_faces import _app, _authenticate, _stub_controller

    _stub_controller(monkeypatch, owner_id="owner-1")
    backend = _Backend()
    app = _app(tmp_path, backend)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local") as client:
        assert (await client.get("/api/management/v1/speech-voices")).status_code == 401
        headers = await _authenticate(client)
        first = await client.get("/api/management/v1/speech-voices", headers=headers)
        assert first.status_code == 200 and "ref" not in first.json()["voices"][0]
        again = await client.get("/api/management/v1/speech-voices",
                                 headers={**headers, "If-None-Match": first.headers["etag"]})
        assert again.status_code == 304
        audio = await client.get(f"/api/management/v1/speech-voices/{catalog_voice_key(OTHER)}/preview",
                                 headers=headers)
        assert audio.headers["content-type"] == "audio/wav" and audio.content == b"RIFF-wav"
        upload = await client.post("/api/management/v1/owner-voices/references/check?start_ms=5000",
                                   headers={**headers, "Content-Type": "audio/mpeg"}, content=b"ID3 mp3 bytes")
        assert upload.json()["window_start_ms"] == 5000 and upload.json()["audio_base64"] == WAV
        forwarded = backend.seen[-1]
        assert forwarded["content"] == b"ID3 mp3 bytes" and forwarded["params"] == {"start_ms": "5000"}
