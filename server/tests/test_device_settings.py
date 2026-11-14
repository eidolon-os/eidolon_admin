"""A device's settings reach the phone through Local API, Admin and the Provider.

Each hop adds only what it owns: Local API the Owner bound to the Controller
session, Admin the Provider credential, and the Provider every decision. A
refusal travels back with the Provider's own code and sentence.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from eidolon_admin_server.app.management.device_settings_router import router
from eidolon_admin_server.local_api.management.backend import AdminManagementClient
from eidolon_admin_server.local_api.management.router import (
    ManagementBackendError,
    refusal_for_status,
)

from tests.test_management_devices import (
    _KNOWN,
    _app,
    _authenticate,
    _Backend,
    _device,
    _Devices,
    _stub_controller,
)

_PREFIX = "/api/management/v1/devices"


def _admin(provider) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.state.settings = SimpleNamespace(local_api_service_token="test-service-token")
    app.state.registry = {"channel-provider": SimpleNamespace(
        base_url="http://channel", upstream_prefix="", auth=SimpleNamespace(type="none"))}
    app.state.provider = provider
    return app


async def _call(provider, method: str, path: str, **kwargs) -> httpx.Response:
    app = _admin(provider)
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as upstream:
        app.state.http_client = upstream
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://admin"
        ) as client:
            assert (await client.request(method, path, **kwargs)).status_code == 401
            return await client.request(
                method, path, headers={"Authorization": "Bearer test-service-token"}, **kwargs
            )


async def test_admin_forwards_an_invocation_with_the_owner_it_was_given():
    seen = {}

    async def provider(request):
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "operation": "channel.device-capability", "device_id": "dev-1",
            "capability": "audio.volume.set",
            "execution": {"execution_id": "owner:x", "state": "completed", "sequence": 1,
                          "result": {"volume": 30}, "error": ""},
        })

    result = await _call(
        provider, "POST", "/internal/v1/management/devices/dev-1/capabilities/audio.volume.set",
        json={"owner_id": "owner-1", "request_id": "slider-1", "arguments": {"volume": 30}},
    )
    assert result.status_code == 200
    assert result.json()["execution"]["result"] == {"volume": 30}
    assert seen["path"] == "/v1/device-channels/dev-1/capabilities/audio.volume.set"
    assert seen["body"] == {"owner_id": "owner-1", "request_id": "slider-1",
                            "arguments": {"volume": 30}}


@pytest.mark.parametrize(
    ("status", "code"),
    [(403, "FORBIDDEN"), (404, "NOT_FOUND"), (409, "CAPABILITY_NOT_OFFERED"), (422, "INVALID")],
)
async def test_admin_relays_the_providers_refusal_unchanged(status, code):
    async def provider(request):
        return httpx.Response(status, json={"code": code, "detail": f"because {code}"})

    result = await _call(
        provider, "GET", "/internal/v1/management/devices/dev-1/properties?owner_id=owner-1",
    )
    assert result.status_code == status
    assert result.json()["detail"] == {"code": code, "detail": f"because {code}"}


async def test_an_upstream_that_cannot_answer_is_unavailable_not_the_clients_fault():
    async def provider(request):
        return httpx.Response(502, text="bad gateway")

    result = await _call(
        provider, "GET", "/internal/v1/management/devices/dev-1/properties?owner_id=owner-1",
    )
    assert result.status_code == 503
    assert result.json()["detail"]["code"] == "CHANNEL_PROVIDER_UNAVAILABLE"


async def test_the_local_api_client_speaks_the_internal_routes():
    calls = []

    async def admin(request):
        calls.append((request.method, request.url.raw_path.decode().split("?")[0],
                      dict(request.url.params),
                      json.loads(request.content) if request.content else None))
        if request.method == "GET":
            return httpx.Response(200, json={"values": {"audio.volume": 20}})
        return httpx.Response(200, json={"execution": {"state": "completed"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(admin)) as http:
        client = AdminManagementClient(
            base_url="http://admin", service_token="internal-test-token",
            client=http, timeout_seconds=5,
        )
        await client.device_properties(owner_id="owner-1", device_id="dev/1")
        await client.invoke_device_capability(
            owner_id="owner-1", device_id="dev/1", capability="audio.volume.set",
            request_id="r-1", arguments={"volume": 5},
        )
    assert calls == [
        ("GET", "/api/internal/v1/management/devices/dev%2F1/properties",
         {"owner_id": "owner-1"}, None),
        ("POST", "/api/internal/v1/management/devices/dev%2F1/capabilities/audio.volume.set",
         {}, {"owner_id": "owner-1", "request_id": "r-1", "arguments": {"volume": 5}}),
    ]


class _SettingsBackend(_Backend):
    def __init__(self, *, refuse: ManagementBackendError | None = None) -> None:
        super().__init__()
        self.refuse = refuse
        self.invocations: list[dict] = []

    async def device_properties(self, *, owner_id, device_id):
        if self.refuse:
            raise self.refuse
        return {"operation": "channel.device-properties", "device_id": device_id,
                "values": {"audio.volume": 35}}

    async def invoke_device_capability(self, **call):
        if self.refuse:
            raise self.refuse
        self.invocations.append(call)
        return {"execution": {"execution_id": "owner:x", "state": "completed", "sequence": 1,
                              "result": {"volume": call["arguments"]["volume"]}, "error": ""}}


async def _phone(tmp_path, monkeypatch, backend):
    _stub_controller(monkeypatch)
    transport = httpx.ASGITransport(app=_app(tmp_path, _Devices(_device()), backend))
    return httpx.AsyncClient(transport=transport, base_url="https://local.test")


async def test_the_phone_sets_a_volume_for_the_owner_of_its_session(tmp_path, monkeypatch):
    backend = _SettingsBackend()
    async with await _phone(tmp_path, monkeypatch, backend) as client:
        path = f"{_PREFIX}/{_KNOWN}/capabilities/audio.volume.set"
        body = {"request_id": "slider-7", "arguments": {"volume": 42}}
        assert (await client.post(path, json=body)).status_code == 401
        answer = await client.post(path, json=body, headers=await _authenticate(client))
    assert answer.status_code == 200
    assert answer.json() == {"device_id": _KNOWN, "capability": "audio.volume.set",
                             "state": "completed", "result": {"volume": 42}, "error": ""}
    # The Owner comes from the session, never from the request.
    assert backend.invocations == [{
        "owner_id": "owner-1", "device_id": _KNOWN, "capability": "audio.volume.set",
        "request_id": "slider-7", "arguments": {"volume": 42},
    }]


async def test_the_phone_reads_only_what_the_device_reported(tmp_path, monkeypatch):
    async with await _phone(tmp_path, monkeypatch, _SettingsBackend()) as client:
        answer = await client.get(
            f"{_PREFIX}/{_KNOWN}/properties", headers=await _authenticate(client)
        )
    assert answer.status_code == 200
    assert answer.json() == {"device_id": _KNOWN, "values": {"audio.volume": 35}}


async def test_a_refusal_reaches_the_phone_with_its_reason(tmp_path, monkeypatch):
    refusal = ManagementBackendError(
        "device does not offer audio.volume.set", status_code=409,
        refusal=refusal_for_status(409, "device does not offer audio.volume.set",
                                   "CAPABILITY_NOT_OFFERED"),
    )
    async with await _phone(tmp_path, monkeypatch, _SettingsBackend(refuse=refusal)) as client:
        answer = await client.post(
            f"{_PREFIX}/{_KNOWN}/capabilities/audio.volume.set",
            json={"request_id": "r", "arguments": {"volume": 1}},
            headers=await _authenticate(client),
        )
    assert answer.status_code == 409
    assert "device does not offer audio.volume.set" in json.dumps(answer.json(), ensure_ascii=False)


async def test_a_request_id_must_be_a_stable_token(tmp_path, monkeypatch):
    async with await _phone(tmp_path, monkeypatch, _SettingsBackend()) as client:
        answer = await client.post(
            f"{_PREFIX}/{_KNOWN}/capabilities/audio.volume.set",
            json={"request_id": "has spaces", "arguments": {"volume": 1}},
            headers=await _authenticate(client),
        )
    assert answer.status_code == 422
