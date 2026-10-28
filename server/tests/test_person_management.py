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
            await c.post(
                base + "/" + person.person_id + "/archive",
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
            return {"persons": []} if kwargs["method"] == "GET" else {"archived": True}

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
            await c.post("/persons/person/archive?expected_revision=7&owner_id=forged")
        ).status_code == 200
        assert (
            calls[-1]["owner_id"] == "claimed-owner"
            and calls[-1]["expected_revision"] == 7
        )
        assert (await c.post("/persons/person/archive")).status_code == 422


async def test_memory_management_routes_derive_owner_and_keep_revision():
    calls = []
    class Backend:
        async def person_call(self, **kwargs):
            calls.append(kwargs)
            return dict(owner_id="claimed-owner", person_id="p", person_revision=3,
                        person_status="active", realm_id="r", state="inactive")
    async def auth(value):
        if value != "Bearer controller":
            raise HTTPException(401)
        return "claimed-owner"
    router = APIRouter()
    register_person_routes(router, Backend(), auth, lambda exc: exc)
    app = FastAPI()
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local") as c:
        assert (await c.get("/persons/p/memory-state")).status_code == 401
        c.headers["Authorization"] = "Bearer controller"
        assert (await c.get("/persons/p/memory-state?owner_id=forged")).status_code == 200
        assert calls[-1]["owner_id"] == "claimed-owner"
        for action in ("suspend", "restore"):
            path = f"/persons/p/memory-realm/{action}"
            assert (await c.post(path)).status_code == 422
            assert (await c.post(path + "?expected_revision=2&owner_id=forged")).status_code == 200
            assert calls[-1]["expected_revision"] == 2
            assert calls[-1]["owner_id"] == "claimed-owner"
            assert calls[-1]["suffix"] == f"/p/memory-realm/{action}"
        assert (await c.post("/persons/p/memory-realm/delete?expected_revision=2")).status_code == 422


async def test_face_management_keeps_authenticated_owner_and_face_revision():
    calls = []
    class Backend:
        async def person_call(self, **kwargs):
            calls.append(kwargs)
            if kwargs['suffix'].endswith('/image'): return {'revision': 2, 'image_base64': None}
            return {'person_id':'p','revision':2,'has_image':False}
    async def auth(value):
        if value != 'Bearer controller': raise HTTPException(401)
        return 'claimed-owner'
    router = APIRouter()
    register_person_routes(router, Backend(), auth, lambda e: e)
    app = FastAPI(); app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://local') as c:
        path = '/persons/p/face-profile'
        assert (await c.get(path)).status_code == 401
        c.headers['Authorization'] = 'Bearer controller'
        for suffix in ('', '/image'):
            res = await c.get(path+suffix+'?owner_id=forged')
            assert res.status_code == 200 and res.headers['cache-control'] == 'no-store'
            assert calls[-1]['owner_id'] == 'claimed-owner'
        body = {'expected_revision':1,'image_base64':'YQ=='}
        assert (await c.put(path+'?owner_id=forged',json=body)).status_code == 200
        assert calls[-1]['owner_id'] == 'claimed-owner' and calls[-1]['payload'] == body
        assert (await c.delete(path+'?expected_revision=2')).status_code == 200
        assert calls[-1]['expected_revision'] == 2
        assert (await c.delete(path)).status_code == 422


async def test_retirement_bridge_derives_owner_and_forwards_revision():
    calls = []
    person = PersonView(person_id='p', owner_id='claimed', display_name='匿名',
                        status='active', revision=4, profile=PersonProfile(),
                        maturity='candidate', is_named=False, recognition_state='retained')

    class Data:
        async def person_call(self, method, owner, suffix, model, payload=None, params=None):
            calls.append((method, owner, suffix, params))
            return person

    internal = FastAPI()
    internal.include_router(people)
    internal.state.settings = SimpleNamespace(local_api_service_token='secret')
    internal.state.control_plane = SimpleNamespace(data=Data())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=internal), base_url='http://admin') as admin:
        class Backend:
            async def person_call(self, **kw):
                response = await admin.request(kw['method'], '/internal/v1/management/persons' + kw['suffix'],
                    headers={'Authorization': 'Bearer secret'},
                    params={'owner_id': kw['owner_id'], 'expected_revision': kw['expected_revision']})
                response.raise_for_status()
                return response.json()

        async def auth(value):
            if value != 'Bearer controller':
                raise HTTPException(401)
            return 'claimed'

        routes = APIRouter()
        register_person_routes(routes, Backend(), auth, lambda exc: exc)
        public = FastAPI()
        public.include_router(routes)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=public), base_url='http://phone') as phone:
            path = '/persons/p/recognition/retire?expected_revision=3&owner_id=forged'
            assert (await phone.post(path)).status_code == 401
            response = await phone.post(path, headers={'Authorization': 'Bearer controller'})
            assert response.status_code == 200
            assert response.json()['recognition_state'] == 'retained'
            assert calls == [('POST', 'claimed', '/p/recognition/retire', {'expected_revision': '3'})]


async def test_provisional_policy_bridge_keeps_owner_revision_and_independent_flags():
    calls = []
    class Data:
        async def person_call(self, method, owner, suffix, model, payload=None, params=None):
            calls.append((method, owner, suffix, payload, params))
            return model.model_validate(dict(owner_id=owner, person_id='p', person_revision=4,
                realm_id='r', policy=payload or dict(save_allowed=False, session_recall_allowed=True)))
    internal = FastAPI()
    internal.include_router(people)
    internal.state.settings = SimpleNamespace(local_api_service_token='secret')
    internal.state.control_plane = SimpleNamespace(data=Data())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=internal), base_url='http://admin') as admin:
        class Backend:
            async def person_call(self, **kw):
                params = {'owner_id': kw['owner_id']}
                if kw['expected_revision'] is not None:
                    params['expected_revision'] = kw['expected_revision']
                result = await admin.request(kw['method'], '/internal/v1/management/persons' + kw['suffix'],
                    headers={'Authorization': 'Bearer secret'}, params=params, json=kw['payload'])
                result.raise_for_status()
                return result.json()
        async def auth(value):
            if value != 'Bearer controller':
                raise HTTPException(401)
            return 'claimed'
        routes = APIRouter()
        register_person_routes(routes, Backend(), auth, lambda exc: exc)
        public = FastAPI()
        public.include_router(routes)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=public), base_url='http://phone') as phone:
            path = '/persons/p/provisional-memory/policy'
            body = {'save_allowed': True, 'session_recall_allowed': False}
            assert (await phone.get(path)).status_code == 401
            assert (await phone.put(path + '?expected_revision=3', json=body)).status_code == 401
            phone.headers['Authorization'] = 'Bearer controller'
            assert (await phone.get(path + '?owner_id=forged')).status_code == 200
            assert (await phone.put(path, json=body)).status_code == 422
            assert (await phone.put(path + '?expected_revision=3&owner_id=forged', json=body)).status_code == 200
            assert calls[-1] == ('PUT', 'claimed', '/p/provisional-memory/policy', body, {'expected_revision': '3'})
            assert all(call[1] == 'claimed' for call in calls)
            assert (await phone.get('/persons/p/provisional-memory/access')).status_code == 404
