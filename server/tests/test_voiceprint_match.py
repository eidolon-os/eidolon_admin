"""Match testing is owner-scoped, read-only and never grants admission."""

import base64
from types import SimpleNamespace
import httpx
import pytest
from fastapi import FastAPI, HTTPException
from eidolon_sdk.biz.voiceprint import (
    ExtractedSample,
    VoiceTemplate,
    PersonVoiceTemplates,
)
from eidolon_admin_server.app.management.voiceprints_router import router


@pytest.mark.parametrize(
    "case,expected",
    [
        ("ok", 200),
        ("empty", 409),
        ("foreign", 404),
        ("changed", 409),
        ("wrong_scope", 404),
        ("unavailable", 503),
    ],
)
async def test_match_is_read_only_and_checks_current_authority(
    monkeypatch, case, expected
):
    monkeypatch.setenv("MATCH_CHANNEL_TOKEN", "secret")
    sample = ExtractedSample(
        model_id="test",
        weights_hash="a" * 64,
        preprocessing_version="v1",
        vector=[1, 0],
        speech_ms=3000,
    )
    template = VoiceTemplate(profile_id="p", revision=1, sample=sample)
    snapshot = PersonVoiceTemplates(
        person_id="c",
        owner_id="owner",
        person_revision=2,
        templates=[] if case == "empty" else [template],
    )
    reads, upstream_calls = [], []

    class Data:
        async def voiceprint_call(self, method, owner, suffix, model, payload=None):
            assert method == "GET" and owner == "owner" and suffix == "/persons/c/templates"
            if case == "foreign":
                raise HTTPException(404)
            reads.append("snapshot")
            if case == "wrong_scope":
                return snapshot.model_copy(update={"owner_id": "other"})
            if case == "changed" and len(reads) > 1:
                return snapshot.model_copy(update={"person_revision": 3, "templates": []})
            return snapshot

    def handle(request):
        assert request.headers["Authorization"] == "Bearer secret"
        upstream_calls.append(request.url.path)
        if case == "unavailable":
            return httpx.Response(503)
        if request.url.path.endswith("/extract"):
            return httpx.Response(200, json=sample.model_dump())
        return httpx.Response(
            200,
            json=dict(
                model_id="test",
                weights_hash="a" * 64,
                preprocessing_version="v1",
                scores=[
                    dict(profile_id="p", revision=1, status="scored", similarity=0.72)
                ],
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as upstream:
        app = FastAPI()
        app.include_router(router)
        app.state.settings = SimpleNamespace(local_api_service_token="local")
        app.state.control_plane = SimpleNamespace(data=Data())
        app.state.http_client = upstream
        app.state.registry = {
            "channel-provider": SimpleNamespace(
                base_url="http://channel",
                upstream_prefix="",
                auth=SimpleNamespace(type="bearer", token_env="MATCH_CHANNEL_TOKEN"),
            )
        }
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://admin"
        ) as h:
            path = "/internal/v1/management/voiceprints/persons/c/match-test?owner_id=owner"
            body = {"pcm_base64": base64.b64encode(b"\x01\x00" * 48000).decode()}
            assert (await h.post(path, json=body)).status_code == 401
            assert not reads and not upstream_calls
            h.headers["Authorization"] = "Bearer local"
            assert (
                await h.post(path, json={**body, "vector": [1, 0]})
            ).status_code == 422
            response = await h.post(path, json=body)
            assert response.status_code == expected, response.text
            if expected == 200:
                result = response.json()
                assert result["person_id"] == "c"
                assert result["scores"][0]["similarity"] == 0.72
                assert result["validation_state"] == "unvalidated"
                assert result["audio_ms"] == 3000
                assert result["elapsed_ms"] >= result["extraction_ms"] >= 0
                assert response.headers["cache-control"] == "no-store"
                assert "vector" not in result and "pcm_base64" not in result
            if case in ("empty", "foreign", "wrong_scope"):
                assert not upstream_calls


async def test_public_match_authenticates_owner_and_never_accepts_templates():
    from fastapi import APIRouter
    from eidolon_admin_server.local_api.management.voiceprints import (
        register_voiceprint_routes,
    )

    calls = []

    class Backend:
        async def voiceprint_call(self, **kwargs):
            calls.append(kwargs)
            return dict(
                person_id="c",
                person_revision=1,
                audio_ms=3000,
                extraction_ms=10,
                elapsed_ms=20,
                model_id="test",
                weights_hash="a" * 64,
                preprocessing_version="v1",
                scores=[
                    dict(profile_id="p", revision=1, status="scored", similarity=0.5)
                ],
            )

    async def auth(value):
        if value != "Bearer controller":
            raise HTTPException(401)
        return "real-owner"

    routes = APIRouter()
    register_voiceprint_routes(routes, Backend(), auth, lambda exc: exc)
    app = FastAPI()
    app.include_router(routes)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as h:
        path = "/voiceprints/persons/c/match-test?owner_id=attacker"
        body = {"pcm_base64": base64.b64encode(b"\x01\x00" * 48000).decode()}
        assert (await h.post(path, json=body)).status_code == 401
        h.headers["Authorization"] = "Bearer controller"
        assert (await h.post(path, json=body | {"templates": []})).status_code == 422
        response = await h.post(path, json=body)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert len(calls) == 1 and calls[0]["owner_id"] == "real-owner"
        assert calls[0]["suffix"] == "/persons/c/match-test"
