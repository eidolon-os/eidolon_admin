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
            return {
                "companion_id": "c",
                "mode": "off",
                "revision": 0,
                "profiles": [],
                "allowed_profile_ids": [],
            }

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
        assert (await h.get("/companions/c/voiceprints")).status_code == 401
        h.headers["Authorization"] = "Bearer controller"
        assert (
            await h.get("/companions/c/voiceprints?owner_id=attacker")
        ).status_code == 200
        assert calls[-1]["owner_id"] == "owner-from-session"
        r = await h.put(
            "/companions/c/voiceprints/enrollments/e/samples/0", json={"vector": [1, 0]}
        )
        assert r.status_code == 422
        assert len(calls) == 1


async def test_admin_extracts_through_authenticated_channel_before_data_publication(
    monkeypatch,
):
    monkeypatch.setenv("VOICEPRINT_TEST_CHANNEL_TOKEN", "channel-secret")
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from eidolon_admin_server.app.management.voiceprints_router import router
    from eidolon_sdk.biz.voiceprint import EnrollmentView

    e = EnrollmentView(
        enrollment_id="e",
        profile_id="p",
        state="collecting",
        accepted_samples=1,
        expires_at=9999999999,
    )
    data = SimpleNamespace(
        get_owner_companion=AsyncMock(), voiceprint_call=AsyncMock(return_value=e)
    )
    requests = []

    def handle(request):
        requests.append(request)
        assert request.headers["Authorization"] == "Bearer channel-secret"
        return httpx.Response(
            200,
            json=dict(
                model_id="test",
                weights_hash="a" * 64,
                preprocessing_version="v1",
                vector=[1, 0],
                speech_ms=4000,
            ),
        )

    upstream = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    app = FastAPI()
    app.include_router(router)
    app.state.settings = SimpleNamespace(local_api_service_token="local-secret")
    app.state.control_plane = SimpleNamespace(data=data)
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
        path = "/internal/v1/management/companions/c/voiceprints/enrollments/e/samples/0?owner_id=owner"
        assert (await h.put(path, json={"pcm_base64": "AAAA"})).status_code == 401
        r = await h.put(
            path,
            headers={"Authorization": "Bearer local-secret"},
            json={"pcm_base64": "AAAA"},
        )
        assert r.status_code == 200, r.text
        assert len(requests) == 1
        data.get_owner_companion.assert_awaited_once_with("owner", "c")
        args = data.voiceprint_call.await_args.args
        assert args[0] == "PUT"
        assert args[-1]["vector"] == [1, 0]
        assert "pcm_base64" not in args[-1]
        from eidolon_sdk.biz.voiceprint import SpeakerProfileView
        data.voiceprint_call.return_value = SpeakerProfileView(
            profile_id="p", display_name="甲", revision=1, enrollment_id="e", recording_available=True
        )
        completed = await h.post(
            "/internal/v1/management/companions/c/voiceprints/enrollments/e/complete?owner_id=owner",
            headers={"Authorization": "Bearer local-secret"},
            json={"pcm_base64": "AAAA"},
        )
        assert completed.status_code == 200, completed.text
        body = data.voiceprint_call.await_args.args[-1]
        assert body["sample"]["vector"] == [1, 0]
        assert body["recording"]["pcm_base64"] == "AAAA"
    await upstream.aclose()


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
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as h:
        path = "/companions/c/voiceprints/profiles/p/recording"
        assert (await h.get(path)).status_code == 401
        assert not calls
        response = await h.get(path, headers={"Authorization": "Bearer controller"})
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert calls[-1]["owner_id"] == "session-owner"
        assert response.json()["pcm_base64"] == "AAAA"
