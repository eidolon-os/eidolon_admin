"""A device's tool restriction, from the phone to Hub and back, hop by hop.

The same chain as the device's outputs, and held to the same things:

- the DeviceRef comes from the Owner's inventory, never from the path;
- the Controller session carries the restriction's own scope;
- each hop checks that the answer is about the device it asked about;
- Hub's refusals keep their code, so ``output_policy_required`` (decide the
  outputs first) and a stale revision (read again) stay different answers.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from eidolon_sdk.biz.tools import (
    DeviceToolRestriction,
    DeviceToolRestrictionConfiguration,
    ReadDeviceToolRestriction,
    SetDeviceToolRestriction,
)
from eidolon_sdk.device_foundation.v1 import DeviceRef
from eidolon_sdk.device_foundation.v1.admission_credential import read_admission_credential

from eidolon_admin_server.app.control_plane.clients import HUB_CONTRACT, HubManagementClient
from eidolon_admin_server.app.control_plane.errors import AuthorityFailure
from eidolon_admin_server.app.control_plane.hub_credentials import HubAdminCredentialIssuer
from eidolon_admin_server.app.control_plane.service import ControlPlaneService
from eidolon_admin_server.local_api.app import create_app
from eidolon_admin_server.local_api.config import (
    LocalApiSettings,
    VerifiedOwnerDomainOnboardingTarget,
)
from eidolon_admin_server.local_api.devices import (
    AdminOwnerDevicesClient,
    DeviceInventoryError,
    tool_restriction_decision,
    tool_restriction_query,
)
from eidolon_admin_server.bootstrap.config import BootstrapMode, BootstrapSettings

from tests.test_control_plane_clients import StaticDirectory
from tests.test_control_plane_component import authenticated_request
from tests.test_local_device_companion import (
    _BUSINESS_OWNER,
    _CONTROLLER_ID,
    _DEVICE,
    _OWNER_DOMAIN,
    _Admission,
    _claims,
    _controller_principal,
    _descriptor,
    _Devices,
    _headers,
    _UnusedPort,
)

_REF = _claims().items[0].device_ref
_OTHER_REF = DeviceRef.model_validate({**_REF.model_dump(mode="json"), "claim_generation": 9})
_PATH = f"/api/management/v1/devices/{_DEVICE}/tool-restriction"
_WRITE = "device.tool-restriction.write"


def _query(device_ref=_REF):
    return tool_restriction_query(
        controller_id=_CONTROLLER_ID, owner_domain_id=_OWNER_DOMAIN,
        business_owner_id=_BUSINESS_OWNER, device_ref=device_ref,
    )


def _decision(*denied: str, expected_revision: int = 0):
    return tool_restriction_decision(
        controller_id=_CONTROLLER_ID, owner_domain_id=_OWNER_DOMAIN,
        business_owner_id=_BUSINESS_OWNER, device_ref=_REF,
        expected_revision=expected_revision, denied=denied,
    )


# ----------------------------------------------------------------------------- the phone


class _RestrictingDevices(_Devices):
    """The control plane's device calls, with a tool restriction Hub would keep."""

    def __init__(self, *, refuse: DeviceInventoryError | None = None) -> None:
        super().__init__()
        self.restriction: DeviceToolRestriction | None = None
        self.restriction_refusal = refuse
        self.restriction_decisions: list = []
        self.restriction_queries: list = []

    async def read_tool_restriction(self, *, payload) -> DeviceToolRestrictionConfiguration:
        self.restriction_queries.append(payload)
        return DeviceToolRestrictionConfiguration(
            device_ref=payload.device_ref, restriction=self.restriction
        )

    async def set_tool_restriction(self, *, payload) -> DeviceToolRestriction:
        self.restriction_decisions.append(payload)
        if self.restriction_refusal is not None:
            raise self.restriction_refusal
        self.restriction = DeviceToolRestriction(
            revision=payload.restriction.expected_revision + 1, denied=payload.restriction.denied
        )
        return self.restriction


class _ToolsBackend:
    def __init__(self) -> None:
        self.asked: list[dict] = []

    async def device_tools(self, **call) -> dict:
        self.asked.append(call)
        return {"device_id": call["device_id"], "companion_id": call["companion_id"],
                "tools": [], "restriction": call["restriction"]}

    async def roster(self, *, owner_id, cursor):
        return {"companions": []}

    async def device_presence(self, *, owner_domain_id):
        return {"bodies": []}


def _app(tmp_path: Path, devices, backend=None):
    root = tmp_path / "owner-root.pem"
    signer = tmp_path / "authority-signer.pem"
    for path in (root, signer):
        path.write_text("-----BEGIN CERTIFICATE-----\nMA==\n", encoding="ascii")
    settings = LocalApiSettings(
        bootstrap=BootstrapSettings(
            mode=BootstrapMode.DEVELOPMENT,
            state_dir=tmp_path / "state",
            runtime_dir=tmp_path / "run",
            control_socket=tmp_path / "run/control.sock",
            ble_service_uuid="179e2e95-b1ee-5aa5-8dcf-7519b6c7ac52",
        ),
        device_onboarding_target=VerifiedOwnerDomainOnboardingTarget(
            owner_domain_id=_OWNER_DOMAIN,
            descriptor_uri="https://eidolon-hub.local:9443/.well-known/eidolon-owner-domain",
            descriptor=_descriptor(),
            owner_root_certificate_path=root,
            authority_signing_certificate_path=signer,
        ),
    )
    unused = _UnusedPort()
    return create_app(
        settings,
        workspace_client=unused,  # type: ignore[arg-type]
        runtime_client=unused,  # type: ignore[arg-type]
        devices_client=devices,  # type: ignore[arg-type]
        device_admission_client=_Admission(),  # type: ignore[arg-type]
        host_services_client=unused,  # type: ignore[arg-type]
        management_backend=backend or unused,
    )


async def _phone(tmp_path, monkeypatch, devices, backend=None) -> httpx.AsyncClient:
    _controller_principal(monkeypatch)
    transport = httpx.ASGITransport(app=_app(tmp_path, devices, backend))
    return httpx.AsyncClient(transport=transport, base_url="https://local.test")


async def test_the_phone_restricts_a_device_named_by_the_inventory_not_the_path(tmp_path, monkeypatch):
    devices = _RestrictingDevices()
    async with await _phone(tmp_path, monkeypatch, devices) as client:
        body = {"expected_revision": 0, "denied": ["music.play", "get_weather", "music.play"]}
        assert (await client.put(_PATH, json=body)).status_code == 401
        headers = await _headers(client)
        answer = await client.put(_PATH, headers=headers, json=body)
        elsewhere = await client.put(
            "/api/management/v1/devices/device-instance-someone-elses/tool-restriction",
            headers=headers, json=body,
        )

    assert answer.status_code == 200, answer.text
    assert answer.json() == {"revision": 1, "denied": ["get_weather", "music.play"]}
    [decision] = devices.restriction_decisions
    # The DeviceRef is the Claim's, whole: generations the phone never sent.
    assert decision.restriction.device_ref == _REF
    assert decision.restriction.expected_revision == 0
    assert str(decision.business_owner_id) == _BUSINESS_OWNER
    assert decision.actor.principal_id == _CONTROLLER_ID
    assert _WRITE in decision.actor.granted_scopes
    # A device this Owner does not hold is absent, and nothing was written for it.
    assert elsewhere.status_code == 404
    assert len(devices.restriction_decisions) == 1


@pytest.mark.parametrize(
    ("refusal", "code"),
    [
        (DeviceInventoryError("Admin Device tool restriction decision did not complete",
                              status_code=409, code="output_policy_required"),
         "output_policy_required"),
        (DeviceInventoryError("Admin Device tool restriction decision did not complete",
                              status_code=409),
         None),
    ],
)
async def test_hubs_refusals_reach_the_phone_with_their_code(tmp_path, monkeypatch, refusal, code):
    devices = _RestrictingDevices(refuse=refusal)
    async with await _phone(tmp_path, monkeypatch, devices) as client:
        answer = await client.put(_PATH, headers=await _headers(client),
                                  json={"expected_revision": 3, "denied": []})

    assert answer.status_code == 409
    detail = answer.json()["detail"]
    assert (detail["kind"], detail["code"]) == ("conflict", code)


@pytest.mark.parametrize(
    "body",
    [
        {"expected_revision": 0, "denied": ["Not A Key"]},
        {"expected_revision": 0, "denied": [f"tool_{n}" for n in range(129)]},
        {"expected_revision": -1, "denied": []},
        {"expected_revision": 0, "denied": [], "device_ref": _REF.model_dump(mode="json")},
    ],
)
async def test_a_restriction_the_contract_does_not_allow_is_refused_before_any_hop(
    tmp_path, monkeypatch, body
):
    devices = _RestrictingDevices()
    async with await _phone(tmp_path, monkeypatch, devices) as client:
        answer = await client.put(_PATH, headers=await _headers(client), json=body)

    assert answer.status_code == 422
    assert devices.restriction_decisions == []


async def test_the_device_tools_page_carries_the_restriction_hub_keeps(tmp_path, monkeypatch):
    devices = _RestrictingDevices()
    devices.restriction = DeviceToolRestriction(revision=4, denied=("get_weather",))
    backend = _ToolsBackend()
    async with await _phone(tmp_path, monkeypatch, devices, backend) as client:
        answer = await client.get(f"/api/management/v1/devices/{_DEVICE}/tools",
                                  headers=await _headers(client))

    assert answer.status_code == 200, answer.text
    assert answer.json()["restriction"] == {"revision": 4, "denied": ["get_weather"]}
    [query] = devices.restriction_queries
    assert query.device_ref == _REF
    assert "device.read" in query.actor.granted_scopes
    [asked] = backend.asked
    assert asked["restriction"] == {"revision": 4, "denied": ["get_weather"]}
    assert (asked["owner_id"], asked["device_id"]) == (_BUSINESS_OWNER, _DEVICE)


# ----------------------------------------------------------------------------- Local API → Admin


def _local_client(handler) -> AdminOwnerDevicesClient:
    return AdminOwnerDevicesClient(
        base_url="http://admin", service_token="local-token", timeout_seconds=5,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


async def test_local_api_asks_the_control_plane_for_the_device_it_holds():
    seen = []

    async def admin(request):
        seen.append((request.method, request.url.path))
        if request.method == "POST":
            return httpx.Response(200, json=DeviceToolRestrictionConfiguration(
                device_ref=_REF, restriction=None).model_dump(mode="json"))
        return httpx.Response(200, json={"revision": 1, "denied": ["get_weather"]})

    client = _local_client(admin)
    configuration = await client.read_tool_restriction(payload=_query())
    stored = await client.set_tool_restriction(payload=_decision("get_weather"))

    assert (configuration.restriction, stored.denied) == (None, ("get_weather",))
    base = f"/api/control-plane/v1/owners/{_BUSINESS_OWNER}/devices/{_DEVICE}"
    assert seen == [("POST", f"{base}/tool-restriction-queries"), ("PUT", f"{base}/tool-restriction")]


async def test_local_api_refuses_an_answer_about_another_device_or_another_list():
    async def admin(request):
        if request.method == "POST":
            return httpx.Response(200, json=DeviceToolRestrictionConfiguration(
                device_ref=_OTHER_REF).model_dump(mode="json"))
        return httpx.Response(200, json={"revision": 1, "denied": ["music.play"]})

    client = _local_client(admin)
    with pytest.raises(DeviceInventoryError) as read:
        await client.read_tool_restriction(payload=_query())
    with pytest.raises(DeviceInventoryError) as written:
        await client.set_tool_restriction(payload=_decision("get_weather"))

    assert (read.value.status_code, written.value.status_code) == (502, 502)


async def test_local_api_keeps_the_code_admin_relayed():
    async def admin(request):
        return httpx.Response(409, json={"detail": {
            "authority": "hub", "kind": "conflict", "detail": "device has no output policy yet",
            "code": "output_policy_required", "retryable": False,
            "refusal": {"kind": "conflict", "reason": "device has no output policy yet",
                        "code": "output_policy_required", "retryable": False},
        }})

    with pytest.raises(DeviceInventoryError) as refused:
        await _local_client(admin).set_tool_restriction(payload=_decision())

    assert (refused.value.status_code, refused.value.code) == (409, "output_policy_required")


# ----------------------------------------------------------------------------- Admin → Hub


class _ControlPlane:
    def __init__(self) -> None:
        self.reads, self.writes = [], []

    async def read_device_tool_restriction(self, *, payload):
        self.reads.append(payload)
        return DeviceToolRestrictionConfiguration(device_ref=payload.device_ref)

    async def set_device_tool_restriction(self, *, payload):
        self.writes.append(payload)
        return DeviceToolRestriction(revision=1, denied=payload.restriction.denied)


async def test_the_control_plane_routes_refuse_a_path_that_names_another_device(app):
    control_plane = _ControlPlane()
    app.state.control_plane = control_plane
    base = f"/api/control-plane/v1/owners/{_BUSINESS_OWNER}/devices"
    query = _query().model_dump(mode="json")
    decision = _decision("get_weather").model_dump(mode="json")

    read = await authenticated_request(app, "POST", f"{base}/{_DEVICE}/tool-restriction-queries",
                                       json=query)
    written = await authenticated_request(app, "PUT", f"{base}/{_DEVICE}/tool-restriction",
                                          json=decision)
    wrong_read = await authenticated_request(app, "POST", f"{base}/other/tool-restriction-queries",
                                             json=query)
    wrong_write = await authenticated_request(
        app, "PUT", f"/api/control-plane/v1/owners/owner-else/devices/{_DEVICE}/tool-restriction",
        json=decision)

    assert (read.status_code, written.status_code) == (200, 200)
    assert written.json() == {"revision": 1, "denied": ["get_weather"]}
    assert (wrong_read.status_code, wrong_write.status_code) == (409, 409)
    assert (len(control_plane.reads), len(control_plane.writes)) == (1, 1)


async def test_a_command_without_the_restriction_scope_cannot_be_built():
    from eidolon_admin_server.app.control_plane.contracts import (
        ControllerDeviceToolRestrictionUpdate,
    )

    decision = _decision().model_dump(mode="json")
    decision["actor"]["granted_scopes"] = ["device.read", "device.output-policy.write"]

    with pytest.raises(ValueError, match="lacks device.tool-restriction.write"):
        ControllerDeviceToolRestrictionUpdate.model_validate(decision)


async def test_the_hub_call_carries_the_controller_and_the_device_it_is_about():
    secret = b"s" * 32
    sent = []

    class _Hub:
        async def set_device_tool_restriction(self, *, command, authorization):
            sent.append((command, authorization))
            return DeviceToolRestriction(revision=1, denied=command.denied)

    service = SimpleNamespace(
        hub=_Hub(),
        _admission_issuer=lambda: HubAdminCredentialIssuer(secret=secret, ttl_seconds=60),
    )
    stored = await ControlPlaneService.set_device_tool_restriction(
        service, payload=_decision("vision.look")
    )

    [(command, authorization)] = sent
    credential = read_admission_credential(authorization, secret=secret)
    assert stored.denied == ("vision.look",)
    assert command.device_ref == _REF
    assert credential.target_device_ref == _REF
    assert _WRITE in credential.scopes
    assert str(credential.business_owner_id) == _BUSINESS_OWNER


def _hub(handler) -> HubManagementClient:
    return HubManagementClient(
        directory=StaticDirectory({("hub", "device-authority.http"): ("http://hub.test", HUB_CONTRACT)}),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        timeout_seconds=1,
    )


async def test_admin_reads_and_writes_the_restriction_at_hub():
    seen = []

    async def hub(request):
        seen.append((request.method, request.url.path, request.headers["Authorization"]))
        if request.method == "POST":
            return httpx.Response(200, json=DeviceToolRestrictionConfiguration(
                device_ref=_REF, restriction=DeviceToolRestriction(revision=2, denied=("get_weather",)),
            ).model_dump(mode="json"))
        return httpx.Response(202, json={"revision": 3, "denied": ["get_weather", "music.play"]})

    client = _hub(hub)
    configuration = await client.read_device_tool_restriction(
        query=ReadDeviceToolRestriction(device_ref=_REF), authorization="Bearer read")
    stored = await client.set_device_tool_restriction(
        command=SetDeviceToolRestriction(device_ref=_REF, expected_revision=2,
                                         denied=("music.play", "get_weather")),
        authorization="Bearer write")

    assert configuration.restriction.denied == ("get_weather",)
    assert stored.revision == 3
    assert seen == [
        ("POST", "/api/device-control/v1/tool-restriction-queries", "Bearer read"),
        ("PUT", "/api/device-control/v1/tool-restriction", "Bearer write"),
    ]


async def test_admin_refuses_hubs_answer_about_another_device_or_another_list():
    async def hub(request):
        if request.method == "POST":
            return httpx.Response(200, json=DeviceToolRestrictionConfiguration(
                device_ref=_OTHER_REF).model_dump(mode="json"))
        return httpx.Response(202, json={"revision": 1, "denied": ["music.play"]})

    client = _hub(hub)
    with pytest.raises(AuthorityFailure) as read:
        await client.read_device_tool_restriction(
            query=ReadDeviceToolRestriction(device_ref=_REF), authorization="Bearer x")
    with pytest.raises(AuthorityFailure) as written:
        await client.set_device_tool_restriction(
            command=SetDeviceToolRestriction(device_ref=_REF, expected_revision=0,
                                             denied=("get_weather",)),
            authorization="Bearer x")

    assert (read.value.kind, written.value.kind) == ("contract_violation", "contract_violation")


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"detail": {"code": "output_policy_required", "message": "device has no output policy yet"}},
         "output_policy_required"),
        ({"detail": "tool restriction revision changed"}, None),
    ],
)
async def test_hubs_conflicts_keep_their_code_at_admin(body, code):
    async def hub(request):
        return httpx.Response(409, json=body)

    with pytest.raises(AuthorityFailure) as refused:
        await _hub(hub).set_device_tool_restriction(
            command=SetDeviceToolRestriction(device_ref=_REF, expected_revision=0),
            authorization="Bearer x")

    assert (refused.value.kind, refused.value.status_code, refused.value.code) == ("conflict", 409, code)
