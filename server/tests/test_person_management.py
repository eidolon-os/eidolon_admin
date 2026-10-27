"""Exercise both management hops with actual HTTP contracts and owner binding."""

from types import SimpleNamespace
import httpx
from fastapi import APIRouter, FastAPI, HTTPException
from eidolon_sdk.biz.person import PersonView, PersonProfile
from eidolon_sdk.biz.voiceprint import VoiceprintCollection
from eidolon_admin_server.app.management.persons_router import router as people
from eidolon_admin_server.app.management.voiceprints_router import router as voices
from eidolon_admin_server.local_api.management.persons import register_person_routes


async def test_internal_person_crud_and_voice_delete_forward_exact_authority_arguments():
    person = PersonView(
        person_id="a" * 32,
        owner_id="owner",
        display_name="Alice",
        status="active",
        revision=1,
        profile=PersonProfile(),
    )
    calls = []

    class Data:
        async def person_call(
            self, method, owner, suffix, model, payload=None, params=None
        ):
            calls.append((method, owner, suffix, payload, params))
            if method == "DELETE":
                return None
            if method == "GET" and not suffix:
                return model([person])
            return person

        async def voiceprint_call(self, method, owner, suffix, model, payload=None):
            calls.append((method, owner, suffix, payload, None))
            return VoiceprintCollection(profiles=[])

    app = FastAPI()
    app.include_router(people)
    app.include_router(voices)
    app.state.settings = SimpleNamespace(local_api_service_token="secret")
    app.state.control_plane = SimpleNamespace(data=Data())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://admin"
    ) as c:
        base = "/internal/v1/management/persons"
        assert (await c.get(base, params={"owner_id": "owner"})).status_code == 401
        c.headers["Authorization"] = "Bearer secret"
        assert (await c.get(base, params={"owner_id": "owner"})).json()["persons"][0][
            "person_id"
        ] == person.person_id
        create = {"person_id": person.person_id, "display_name": "Alice"}
        assert (
            await c.post(base, params={"owner_id": "owner"}, json=create)
        ).status_code == 200
        assert (
            await c.put(
                base + "/" + person.person_id,
                params={"owner_id": "owner"},
                json={
                    "display_name": "Alice",
                    "expected_revision": 1,
                    "status": "inactive",
                    "profile": {},
                },
            )
        ).status_code == 200
        assert (
            await c.delete(
                base + "/" + person.person_id,
                params={"owner_id": "owner", "expected_revision": 1},
            )
        ).status_code == 200
        assert calls[-1][-1] == {"expected_revision": "1"}
        assert (
            await c.delete(
                "/internal/v1/management/voiceprints/profiles/profile",
                params={"owner_id": "owner"},
            )
        ).status_code == 200
        assert calls[-1][:3] == ("DELETE", "owner", "/profiles/profile")


async def test_local_person_management_derives_owner_and_preserves_delete_revision():
    calls = []

    class Backend:
        async def person_call(self, **kwargs):
            calls.append(kwargs)
            return {"persons": []} if kwargs["method"] == "GET" else {"deleted": True}

    async def auth(value):
        if value != "Bearer controller":
            raise HTTPException(401)
        return "claimed-owner"

    router = APIRouter()
    register_person_routes(router, Backend(), auth, lambda exc: exc)
    app = FastAPI()
    app.include_router(router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://local"
    ) as c:
        assert (await c.get("/persons")).status_code == 401
        c.headers["Authorization"] = "Bearer controller"
        assert (await c.get("/persons?owner_id=forged")).status_code == 200
        assert calls[-1]["owner_id"] == "claimed-owner"
        assert (
            await c.delete("/persons/person?expected_revision=7&owner_id=forged")
        ).status_code == 200
        assert (
            calls[-1]["owner_id"] == "claimed-owner"
            and calls[-1]["expected_revision"] == 7
        )
        assert (await c.delete("/persons/person")).status_code == 422
