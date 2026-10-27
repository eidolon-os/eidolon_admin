import httpx
from fastapi import APIRouter, FastAPI, HTTPException
from eidolon_admin_server.local_api.management.voiceprints import (
    register_voiceprint_routes,
)


async def test_voiceprints_use_authenticated_owner_and_never_accept_client_vectors():
    calls = []

    class Backend:
        async def voiceprint_call(self, **kwargs):
            calls.append(kwargs)
            return {"profiles": []}


    async def auth(value):
        if value != "Bearer controller":
            raise HTTPException(401)
        return "owner-from-session"

    router = APIRouter()
    register_voiceprint_routes(router, Backend(), auth, lambda exc: exc)
    app = FastAPI()
    app.include_router(router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as h:
        assert (await h.get("/voiceprints")).status_code == 401
        h.headers["Authorization"] = "Bearer controller"
        assert (
            await h.get("/voiceprints?owner_id=attacker")
        ).status_code == 200
        assert calls[-1]["owner_id"] == "owner-from-session"
        r = await h.put(
            "/voiceprints/enrollments/e/samples/0", json={"vector": [1, 0]}
        )
        assert r.status_code == 422
        assert len(calls) == 1


async def test_completion_checks_authority_and_recovers_success_without_reextracting(
    monkeypatch,
):
    import base64
    from types import SimpleNamespace
    from eidolon_admin_server.app.management.voiceprints_router import router
    from eidolon_sdk.biz.voiceprint import (
        EnrollmentView,
        EnrollmentEvidence,
        EnrollmentEvaluation,
        ExtractedSample,
        SpeakerProfileView,
        VoiceprintCollection,
        enrollment_evidence_digest,
    )

    monkeypatch.setenv("VOICEPRINT_TEST_CHANNEL_TOKEN", "channel-secret")
    sample = ExtractedSample(
        model_id="test",
        weights_hash="a" * 64,
        preprocessing_version="v1",
        vector=[1, 0],
        speech_ms=4000,
    )
    samples = {str(i): sample for i in range(3)}
    e = EnrollmentView(
        enrollment_id="e",
        profile_id="p",
        state="collecting",
        accepted_samples=3,
        expires_at=9999999999,
    )
    profile = SpeakerProfileView(
        profile_id="p",
        display_name="我",
        revision=1,
        enrollment_id="e",
        recording_available=True,
    )
    requests, stored = [], []

    class Data:
        async def voiceprint_call(
            self, method, owner, suffix, model, payload=None
        ):
            assert owner == "owner"
            if suffix.endswith("/complete"):
                stored.append(payload)
                e.state = "ready"
                return profile
            if suffix.endswith("/evidence"):
                return EnrollmentEvidence(enrollment=e, samples=samples)
            if not suffix:
                return VoiceprintCollection(profiles=[profile])
            return e

    def handle(request):
        import json

        if request.url.path.endswith("/readiness"):
            return httpx.Response(200, json={"state": "ready", "extraction_ready": True})
        requests.append(request.url.path)
        assert request.headers["Authorization"] == "Bearer channel-secret"
        if request.url.path.endswith("/extract"):
            return httpx.Response(200, json=sample.model_dump())
        body = json.loads(request.content)
        result = EnrollmentEvaluation(
            evidence_digest=enrollment_evidence_digest(samples),
            recording_sha256=body["recording_sha256"],
            template=sample,
            trial=sample,
            registration_min_similarity=1,
            trial_min_similarity=1,
        )
        return httpx.Response(200, json=result.model_dump())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as upstream:
        app = FastAPI()
        app.include_router(router)
        app.state.settings = SimpleNamespace(local_api_service_token="local-secret")
        app.state.control_plane = SimpleNamespace(data=Data())
        app.state.http_client = upstream
        app.state.registry = {
            "channel-provider": SimpleNamespace(
                base_url="http://channel",
                upstream_prefix="",
                auth=SimpleNamespace(
                    type="bearer", token_env="VOICEPRINT_TEST_CHANNEL_TOKEN"
                ),
            )
        }
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://admin"
        ) as h:
            path = "/internal/v1/management/voiceprints/enrollments/e/complete?owner_id=owner"
            body = {"pcm_base64": base64.b64encode(b"\x01\x00" * 32000).decode()}
            assert (await h.post(path, json=body)).status_code == 401
            assert not requests
            h.headers["Authorization"] = "Bearer local-secret"
            first = await h.post(path, json=body)
            assert first.status_code == 200, first.text
            again = await h.post(path, json=body)
            assert again.json() == first.json()
            assert len(stored) == 1 and len(requests) == 2
            assert stored[0]["evaluation"][
                "evidence_digest"
            ] == enrollment_evidence_digest(samples)
            assert stored[0]["recording"] == body


async def test_recording_reuses_owner_authentication_and_disables_cache():
    calls = []

    class Backend:
        async def voiceprint_call(self, **kwargs):
            calls.append(kwargs)
            return {"pcm_base64": "AAAA"}

    async def auth(value):
        if value != "Bearer controller":
            raise HTTPException(401)
        return "session-owner"

    router = APIRouter()
    register_voiceprint_routes(router, Backend(), auth, lambda exc: exc)
    app = FastAPI()
    app.include_router(router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as h:
        path = "/voiceprints/profiles/p/recording"
        assert (await h.get(path)).status_code == 401
        assert not calls
        response = await h.get(path, headers={"Authorization": "Bearer controller"})
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert calls[-1]["owner_id"] == "session-owner"
        assert response.json()["pcm_base64"] == "AAAA"


async def test_model_outage_preserves_management_but_prevents_recording_and_admission():
    from types import SimpleNamespace
    from eidolon_admin_server.app.management.voiceprints_router import router
    from eidolon_sdk.biz.voiceprint import VoiceprintCollection

    calls = []

    class Data:
        async def get_owner_companion(self, owner, companion):
            if owner != "owner":
                raise HTTPException(404)

        async def voiceprint_call(
            self, method, owner, suffix, model, payload=None
        ):
            if owner != "owner":
                raise HTTPException(404)
            calls.append((method, suffix))
            return VoiceprintCollection()

    app = FastAPI()
    app.include_router(router)
    app.state.settings = SimpleNamespace(local_api_service_token="local-secret")
    app.state.control_plane = SimpleNamespace(data=Data())
    app.state.registry = {}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://admin",
        headers={"Authorization": "Bearer local-secret"},
    ) as h:
        prefix = "/internal/v1/management/voiceprints"
        view = await h.get(prefix + "?owner_id=owner")
        assert view.status_code == 200
        assert view.json()["readiness"]["extraction_ready"] is False
        assert "admission_ready" not in view.json()["readiness"]
        assert (
            await h.post(
                prefix + "/enrollments?owner_id=owner",
                json={"operation_id": "one", "display_name": "我", "person_id": "p"},
            )
        ).status_code == 503
        assert (
            await h.put(
                prefix + "/policy?owner_id=owner",
                json={"mode": "allowlist", "expected_revision": 0},
            )
        ).status_code == 404
        assert (await h.get(prefix + "/readiness?owner_id=other")).status_code == 404
        assert all(method == "GET" for method, _ in calls)
