"""Exercise both management hops with actual HTTP contracts and owner binding."""

from types import SimpleNamespace
import httpx
import pytest
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
            if kwargs['suffix'].endswith('/image'):
                return {'revision': 2, 'image_base64': None}
            return {'person_id':'p','revision':2,'has_image':False}
    async def auth(value):
        if value != 'Bearer controller':
            raise HTTPException(401)
        return 'claimed-owner'
    router = APIRouter()
    register_person_routes(router, Backend(), auth, lambda e: e)
    app = FastAPI()
    app.include_router(router)
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


async def test_owner_defaults_bridge_keeps_controller_owner_and_owner_revision():
    calls = []
    class Data:
        async def person_call(self, method, owner, suffix, model, payload=None, params=None):
            calls.append((method, owner, suffix, payload, params))
            return model.model_validate(dict(owner_id=owner, owner_revision=4,
                policy=payload or dict(candidate={}, prepare_on_promotion=False)))
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
            path = '/persons/memory-policy'
            body = {'candidate': {'save_allowed': True, 'session_recall_allowed': False}, 'prepare_on_promotion': True}
            assert (await phone.get(path)).status_code == 401
            assert (await phone.put(path + '?expected_revision=3', json=body)).status_code == 401
            phone.headers['Authorization'] = 'Bearer controller'
            assert (await phone.get(path + '?owner_id=forged')).status_code == 200
            assert (await phone.put(path, json=body)).status_code == 422
            assert (await phone.put(path + '?expected_revision=3&owner_id=forged', json=body)).status_code == 200
            # Forwarded as the full policy, so an omitted field carries its default.
            assert calls[-1] == ('PUT', 'claimed', '/memory-policy', {**body, 'household_memory': True},
                                 {'expected_revision': '3'})
            assert all(call[1] == 'claimed' for call in calls)
            assert (await phone.get('/persons/p/provisional-memory/access')).status_code == 404


CONSENT = {"consent_version": "face-1", "statement": "They agreed.", "attested": True}
FACE_STATE = {"person_id": "p", "revision": 3, "has_image": True}
VECTOR = [0.25, 0.5, -0.125]


def _recognition_app(vision_answer, *, image_revision=3, image="/9j/", vision_url="http://vision",
                     vision_token="vision-token", directory=None):
    """The internal hop with a fake Data and the real Vision client over a fake wire."""
    import base64

    from eidolon_admin_server.app.control_plane.clients import VisionFaceClient
    from eidolon_admin_server.app.control_plane.failure_handler import (
        install_authority_failure_handler,
    )

    data_calls, vision_requests = [], []

    class Data:
        async def person_call(self, method, owner, suffix, model, payload=None, params=None):
            data_calls.append((method, owner, suffix, payload, params))
            if suffix.endswith("/image"):
                return model(revision=image_revision, image_base64=image,
                             content_type="image/jpeg" if image else None)
            enabled = method == "PUT"
            return model(**FACE_STATE, recognition_state="enabled" if enabled else "not_configured")

    def answer(request):
        vision_requests.append(request)
        if isinstance(vision_answer, Exception):
            raise vision_answer
        return vision_answer

    app = FastAPI()
    install_authority_failure_handler(app)
    app.include_router(people)
    app.state.settings = SimpleNamespace(local_api_service_token="secret")
    vision = VisionFaceClient(
        base_url=vision_url, service_token=vision_token, timeout_seconds=5, directory=directory,
        client=httpx.AsyncClient(transport=httpx.MockTransport(answer)))
    app.state.control_plane = SimpleNamespace(data=Data(), vision=vision)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://admin",
                               headers={"Authorization": "Bearer secret"})
    assert base64.b64decode("/9j/") == b"\xff\xd8\xff"
    return client, data_calls, vision_requests


def _embedded(**changes):
    return httpx.Response(200, json={
        "accepted": True, "faces": 1, "rejection": None, "face_px": 200, "model_id": "face-model",
        "weights_hash": "c" * 64, "preprocessing_version": "prep-1", "feature_schema_version": 1,
        "vector": VECTOR, **changes})


RECOGNITION = "/internal/v1/management/persons/p/face-profile/recognition"


async def test_enabling_recognition_sends_the_stored_photo_to_vision_and_the_feature_to_data(caplog):
    client, data_calls, vision_requests = _recognition_app(_embedded())
    body = {"expected_revision": 3,
            "consent": {**CONSENT, "accepted_at": "1999-01-01T00:00:00+00:00"}}
    with caplog.at_level("DEBUG"):
        async with client as c:
            assert (await c.put(RECOGNITION, json=body)).status_code == 422  # owner_id is required
            response = await c.put(RECOGNITION, params={"owner_id": "owner"}, json=body)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert response.json()["recognition_state"] == "enabled"
    assert "vector" not in response.text and "0.125" not in response.text
    [sent] = vision_requests
    assert sent.url == "http://vision/v1/faces/embed"
    assert sent.headers["authorization"] == "Bearer vision-token"
    assert sent.headers["content-type"].startswith("multipart/form-data")
    assert b'name="image"' in sent.content and b"\xff\xd8\xff" in sent.content
    assert b'name="owner_id"\r\n\r\nowner' in sent.content
    assert [call[:3] for call in data_calls] == [
        ("GET", "owner", "/p/face-profile/image"), ("PUT", "owner", "/p/face-profile/recognition")]
    written = data_calls[-1][3]
    assert written["expected_revision"] == 3
    # Data stamps the time; what the client claimed is not passed on.
    assert written["consent"] == {**CONSENT, "accepted_at": None}
    assert written["features"] == {
        "model_id": "face-model", "weights_hash": "c" * 64, "preprocessing_version": "prep-1",
        "feature_schema_version": 1, "vector": VECTOR}
    assert "0.125" not in caplog.text and "/9j/" not in caplog.text


@pytest.mark.parametrize("stored", [dict(image_revision=4), dict(image=None)])
async def test_a_photo_changed_since_the_consent_is_never_sent_to_vision(stored):
    client, data_calls, vision_requests = _recognition_app(_embedded(), **stored)
    async with client as c:
        response = await c.put(RECOGNITION, params={"owner_id": "owner"},
                               json={"expected_revision": 3, "consent": CONSENT})
    assert response.status_code == 409
    assert response.json()["detail"]["refusal"]["kind"] == "conflict"
    assert vision_requests == [] and len(data_calls) == 1


@pytest.mark.parametrize("rejection", ["no_face", "multiple_faces", "face_too_small", "low_confidence"])
async def test_a_photo_vision_rejects_is_refused_with_the_reason(rejection):
    client, data_calls, _ = _recognition_app(
        _embedded(accepted=False, rejection=rejection, vector=None))
    async with client as c:
        response = await c.put(RECOGNITION, params={"owner_id": "owner"},
                               json={"expected_revision": 3, "consent": CONSENT})
    assert response.status_code == 422
    assert response.json()["detail"]["refusal"] == {
        "kind": "invalid", "code": "face_photo_rejected", "reason": rejection, "retryable": False}
    assert len(data_calls) == 1  # nothing was written


@pytest.mark.parametrize("answer, url, kind", [
    (_embedded(), "", "not_configured"),
    (httpx.Response(503, json={"detail": "face identity is not configured"}), "http://vision", "not_running"),
    (httpx.Response(500), "http://vision", "not_running"),
    (httpx.Response(401, json={"detail": "service token required"}), "http://vision", "not_configured"),
    (httpx.ConnectError("refused"), "http://vision", "not_running"),
    (httpx.ReadTimeout("slow"), "http://vision", "not_running"),
    (_embedded(weights_hash=""), "http://vision", "upstream"),
    (_embedded(vector=None), "http://vision", "upstream"),
    (_embedded(accepted=False, rejection="something_new", vector=None), "http://vision", "upstream"),
])
async def test_recognition_is_unavailable_whenever_vision_cannot_answer(answer, url, kind):
    client, data_calls, _ = _recognition_app(answer, vision_url=url)
    async with client as c:
        response = await c.put(RECOGNITION, params={"owner_id": "owner"},
                               json={"expected_revision": 3, "consent": CONSENT})
    assert response.status_code == 503
    refusal = response.json()["detail"]["refusal"]
    assert refusal["code"] == "face_recognition_unavailable" and refusal["kind"] == kind
    assert len(data_calls) == 1
    assert "0.125" not in response.text


class _Directory:
    """eidolond's endpoint directory, answering for Vision the way a Host would."""

    def __init__(self, *, failure=None, contract="eidolon.vision.faces.v1"):
        self.failure, self.contract, self.asked = failure, contract, []

    async def resolve(self, *, service_id, endpoint_id, required_contract):
        from eidolon_admin_server.app.control_plane.contracts import ServiceEndpoint

        self.asked.append((service_id, endpoint_id, required_contract))
        if self.failure is not None:
            raise self.failure
        assert required_contract == self.contract
        return ServiceEndpoint(
            operation="system.service-endpoint", service_id=service_id, endpoint_id=endpoint_id,
            protocol="http", address="http://127.0.0.1:18085/", contract=self.contract)


async def test_vision_is_found_through_the_directory_and_needs_no_token():
    # A Host: no address written anywhere and no shared secret. The Directory
    # lists Vision because the Host declares face identity.
    directory = _Directory()
    client, data_calls, vision_requests = _recognition_app(
        _embedded(), vision_url="", vision_token="", directory=directory)
    async with client as c:
        response = await c.put(RECOGNITION, params={"owner_id": "owner"},
                               json={"expected_revision": 3, "consent": CONSENT})
    assert response.status_code == 200 and response.json()["recognition_state"] == "enabled"
    assert directory.asked == [("vision", "face-identity.http", "eidolon.vision.faces.v1")]
    [sent] = vision_requests
    assert sent.url == "http://127.0.0.1:18085/v1/faces/embed"
    assert "authorization" not in sent.headers
    assert len(data_calls) == 2


async def test_an_explicit_vision_address_is_used_without_asking_the_directory():
    directory = _Directory()
    client, _, vision_requests = _recognition_app(_embedded(), directory=directory)
    async with client as c:
        response = await c.put(RECOGNITION, params={"owner_id": "owner"},
                               json={"expected_revision": 3, "consent": CONSENT})
    assert response.status_code == 200 and directory.asked == []
    assert vision_requests[0].headers["authorization"] == "Bearer vision-token"


@pytest.mark.parametrize("status, failure_kind, kind, retryable", [
    # No such service: this Host does not declare face identity.
    (404, "unavailable", "not_configured", False),
    # Listed, and its readiness probe has not passed.
    (503, "unavailable", "not_running", True),
    (None, "unavailable", "not_running", True),  # eidolond itself unreachable
    (None, "contract_violation", "upstream", False),
])
async def test_recognition_is_unavailable_when_the_directory_has_no_vision_to_give(
    status, failure_kind, kind, retryable
):
    from eidolon_admin_server.app.control_plane.errors import AuthorityFailure

    directory = _Directory(failure=AuthorityFailure(
        "directory", failure_kind, "service endpoint is not ready: vision/face-identity.http",
        503 if failure_kind == "unavailable" else 502, status, retryable or status == 404))
    client, data_calls, vision_requests = _recognition_app(
        _embedded(), vision_url="", vision_token="", directory=directory)
    async with client as c:
        response = await c.put(RECOGNITION, params={"owner_id": "owner"},
                               json={"expected_revision": 3, "consent": CONSENT})
    assert response.status_code == 503
    refusal = response.json()["detail"]["refusal"]
    assert refusal["code"] == "face_recognition_unavailable" and refusal["kind"] == kind
    assert refusal["retryable"] is retryable
    assert vision_requests == [] and len(data_calls) == 1


async def test_recognition_without_a_vision_client_is_unavailable_and_disable_passes_through():
    client, data_calls, vision_requests = _recognition_app(_embedded())
    async with client as c:
        del c._transport.app.state.control_plane.vision
        response = await c.put(RECOGNITION, params={"owner_id": "owner"},
                               json={"expected_revision": 3, "consent": CONSENT})
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "face_recognition_unavailable"
        disabled = await c.delete(RECOGNITION, params={"owner_id": "owner"})
    assert disabled.status_code == 200 and disabled.headers["cache-control"] == "no-store"
    assert disabled.json() == {**disabled.json(), **FACE_STATE, "recognition_state": "not_configured"}
    assert data_calls[-1] == ("DELETE", "owner", "/p/face-profile/recognition", None, None)
    assert vision_requests == []


async def test_local_face_recognition_derives_owner_and_relays_the_consent():
    calls = []

    class Backend:
        async def person_call(self, **kwargs):
            calls.append(kwargs)
            return {**FACE_STATE, "recognition_state":
                    "enabled" if kwargs["method"] == "PUT" else "not_configured"}

    async def auth(value):
        if value != "Bearer controller":
            raise HTTPException(401)
        return "claimed-owner"

    router = APIRouter()
    register_person_routes(router, Backend(), auth, lambda exc: exc)
    app = FastAPI()
    app.include_router(router)
    path = "/persons/p/face-profile/recognition"
    body = {"expected_revision": 3, "consent": CONSENT}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local") as c:
        assert (await c.put(path, json=body)).status_code == 401
        assert (await c.delete(path)).status_code == 401
        c.headers["Authorization"] = "Bearer controller"
        enabled = await c.put(path + "?owner_id=forged", json=body)
        assert enabled.status_code == 200 and enabled.headers["cache-control"] == "no-store"
        assert enabled.json()["recognition_state"] == "enabled"
        assert calls[-1] == dict(owner_id="claimed-owner", suffix="/p/face-profile/recognition",
                                 method="PUT", expected_revision=None,
                                 payload={**body, "consent": {**CONSENT, "accepted_at": None}})
        for refused in ({"expected_revision": 3}, {**body, "features": {"vector": VECTOR}},
                        {**body, "consent": {**CONSENT, "attested": False}}):
            assert (await c.put(path, json=refused)).status_code == 422
        disabled = await c.delete(path + "?owner_id=forged")
        assert disabled.status_code == 200 and disabled.json()["recognition_state"] == "not_configured"
        assert calls[-1]["method"] == "DELETE" and calls[-1]["owner_id"] == "claimed-owner"
        assert calls[-1]["expected_revision"] is None and calls[-1]["payload"] is None
