"""Channel calibration by replaying public speech (design §8.2, 1c-a).

The phone records a public speaker through the registration capture path;
Admin extracts it exactly like a registration sample and hands Data the
embedding and its windows. No audio reaches Data, and the phone chooses
neither the channel nor the vectors.
"""

import base64
import json
import time
from types import SimpleNamespace

import httpx
from fastapi import APIRouter, FastAPI, HTTPException
from eidolon_sdk.biz.voiceprint import (
    CalibrationCrop,
    ChannelCalibrationCollection,
    ChannelCalibrationSample,
    EnrollmentSample,
    VoiceprintCollection,
)

from eidolon_admin_server.app.management.voiceprints_router import router
from eidolon_admin_server.local_api.management.voiceprints import (
    register_voiceprint_routes,
)

PREFIX = "/internal/v1/management/voiceprints"
PCM = base64.b64encode(b"\x01\x00" * 128000).decode()  # 8 s at 16 kHz
SAMPLE = EnrollmentSample(
    model_id="test",
    weights_hash="a" * 64,
    preprocessing_version="v1",
    vector=[1, 0],
    speech_ms=6000,
    crops=[CalibrationCrop(speech_ms=900, vector=[0.6, 0.8])],
)
COLLECTION = ChannelCalibrationCollection.model_validate(
    {
        "batches": [
            {
                "batch_id": "replay-1",
                "channel": "mobile",
                "samples": 2,
                "first_captured_at": 1.0,
                "last_captured_at": 2.0,
            }
        ]
    }
)


class Data:
    """Data that knows one Owner and remembers what it was asked to keep."""

    def __init__(self):
        self.calls = []

    async def voiceprint_call(self, method, owner, suffix, model, payload=None):
        if owner != "owner":
            raise HTTPException(404, "not found")
        self.calls.append((method, suffix, payload))
        if suffix == "":
            return VoiceprintCollection()
        if suffix == "/channel-calibration/samples":
            return model.model_validate({"sample_id": "s1", "source": "public_replay", **payload})
        return COLLECTION


def _app(data, handle, monkeypatch):
    monkeypatch.setenv("VOICEPRINT_TEST_CHANNEL_TOKEN", "channel-secret")
    app = FastAPI()
    app.include_router(router)
    app.state.settings = SimpleNamespace(local_api_service_token="local-secret")
    app.state.control_plane = SimpleNamespace(data=data)
    app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    app.state.registry = {
        "channel-provider": SimpleNamespace(
            base_url="http://channel",
            upstream_prefix="",
            auth=SimpleNamespace(type="bearer", token_env="VOICEPRINT_TEST_CHANNEL_TOKEN"),
        )
    }
    return app


async def test_upload_is_extracted_like_registration_and_stored_without_audio(monkeypatch):
    data, extractions = Data(), []

    def handle(request):
        assert request.url.path == "/v1/voiceprints/extract"
        assert request.headers["Authorization"] == "Bearer channel-secret"
        extractions.append(json.loads(request.content))
        return httpx.Response(200, json=SAMPLE.model_dump(mode="json"))

    app = _app(data, handle, monkeypatch)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://admin"
    ) as h:
        path = PREFIX + "/channel-calibration/samples?owner_id=owner"
        body = {"pcm_base64": PCM, "batch_id": "replay-1"}
        assert (await h.post(path, json=body)).status_code == 401
        assert not data.calls and not extractions
        h.headers["Authorization"] = "Bearer local-secret"
        before = time.time()
        response = await h.post(path, json=body)
        after = time.time()
    await app.state.http_client.aclose()

    assert response.status_code == 200, response.text
    saved = ChannelCalibrationSample.model_validate(response.json())
    assert saved.batch_id == "replay-1" and saved.channel == "mobile"
    # Extraction gets the recording only, with the registration quality floor.
    assert extractions == [{"pcm_base64": PCM, "purpose": "enrollment", "min_speech_ms": 1500}]
    method, suffix, stored = data.calls[-1]
    assert (method, suffix) == ("POST", "/channel-calibration/samples")
    assert set(stored) == {"channel", "batch_id", "captured_at", "sample"}
    assert stored["channel"] == "mobile" and stored["batch_id"] == "replay-1"
    assert before <= stored["captured_at"] <= after
    # Windows travel with the embedding, exactly as registration keeps them.
    assert stored["sample"] == SAMPLE.model_dump(mode="json")
    assert PCM not in json.dumps(stored)


async def test_rejected_recording_and_unknown_owner_store_nothing(monkeypatch):
    data, extractions = Data(), []

    def handle(request):
        extractions.append(request.url.path)
        return httpx.Response(422, json={"code": "SPEECH_TOO_SHORT"})

    app = _app(data, handle, monkeypatch)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://admin",
        headers={"Authorization": "Bearer local-secret"},
    ) as h:
        body = {"pcm_base64": PCM, "batch_id": "replay-1"}
        stranger = await h.post(
            PREFIX + "/channel-calibration/samples?owner_id=other", json=body
        )
        assert stranger.status_code == 404
        assert not extractions
        short = await h.post(
            PREFIX + "/channel-calibration/samples?owner_id=owner", json=body
        )
        assert short.status_code == 422
        assert "有效语音太短" in short.json()["detail"]
    await app.state.http_client.aclose()
    assert extractions == ["/v1/voiceprints/extract"]
    assert [call[0] for call in data.calls] == ["GET"]


async def test_batches_are_listed_and_deleted_through_data(monkeypatch):
    data = Data()

    def handle(request):
        raise AssertionError("listing and deleting never extract")

    app = _app(data, handle, monkeypatch)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://admin",
        headers={"Authorization": "Bearer local-secret"},
    ) as h:
        listed = await h.get(PREFIX + "/channel-calibration?owner_id=owner")
        deleted = await h.delete(
            PREFIX + "/channel-calibration/batches/replay-1?owner_id=owner"
        )
        stranger = await h.get(PREFIX + "/channel-calibration?owner_id=other")
    await app.state.http_client.aclose()
    assert listed.status_code == 200 and deleted.status_code == 200
    assert listed.json() == COLLECTION.model_dump(mode="json")
    assert stranger.status_code == 404
    assert [(m, s) for m, s, _ in data.calls] == [
        ("GET", "/channel-calibration"),
        ("DELETE", "/channel-calibration/batches/replay-1"),
    ]


async def test_controller_routes_use_the_session_owner_and_refuse_client_choices():
    calls = []

    class Backend:
        async def voiceprint_call(self, **kwargs):
            calls.append(kwargs)
            if kwargs["suffix"] == "/channel-calibration/samples":
                return ChannelCalibrationSample(
                    sample_id="s1",
                    channel="mobile",
                    batch_id="replay-1",
                    captured_at=1.0,
                    sample=SAMPLE,
                ).model_dump(mode="json")
            return COLLECTION.model_dump(mode="json")

    async def auth(value):
        if value != "Bearer controller":
            raise HTTPException(401)
        return "session-owner"

    api = APIRouter()
    register_voiceprint_routes(api, Backend(), auth, lambda exc: exc)
    app = FastAPI()
    app.include_router(api)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as h:
        assert (await h.get("/voiceprints/channel-calibration")).status_code == 401
        assert not calls
        h.headers["Authorization"] = "Bearer controller"
        body = {"pcm_base64": PCM, "batch_id": "replay-1"}
        for refused in (
            {**body, "channel": "speaker"},
            {**body, "vector": [1, 0]},
            {**body, "batch_id": "../other"},
        ):
            r = await h.post("/voiceprints/channel-calibration/samples", json=refused)
            assert r.status_code == 422
        assert not calls
        uploaded = await h.post(
            "/voiceprints/channel-calibration/samples?owner_id=attacker", json=body
        )
        listed = await h.get("/voiceprints/channel-calibration?owner_id=attacker")
        deleted = await h.delete("/voiceprints/channel-calibration/batches/replay-1")
    assert [r.status_code for r in (uploaded, listed, deleted)] == [200, 200, 200]
    assert [(c["method"], c["suffix"]) for c in calls] == [
        ("POST", "/channel-calibration/samples"),
        ("GET", "/channel-calibration"),
        ("DELETE", "/channel-calibration/batches/replay-1"),
    ]
    assert all(c["owner_id"] == "session-owner" for c in calls)
    assert calls[0]["payload"] == body
