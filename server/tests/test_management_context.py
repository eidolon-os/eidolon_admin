"""The first request a management client makes, and what it may not decide.

Phase 0 puts an empty-but-correct surface in place: one read, no writes, every
mutation capability false. The value is not the endpoint — it is that the shape
is settled before anything depends on it. So most of these tests are about what
the boundary refuses to do:

- it never takes an ``owner_id`` from a caller;
- it reports capabilities as discovery, all false until a slice is closed;
- it relays what the credential-holding process said rather than reinterpreting
  it, because this side holds no credentials and no authority facts.
"""

from __future__ import annotations

from pathlib import Path

from types import SimpleNamespace

import httpx
import pytest

from eidolon_admin_server.app.management.context import (
    _CAPABILITIES,
    _ENABLED,
    read_context,
)
from eidolon_admin_server.bootstrap.config import BootstrapMode, BootstrapSettings
from eidolon_admin_server.bootstrap.control import BootstrapControlClient
from eidolon_admin_server.local_api.app import create_app
from eidolon_admin_server.local_api.config import LocalApiSettings
from eidolon_admin_server.local_api.management.router import ManagementBackendError

pytestmark = pytest.mark.asyncio

_AUTH_CHALLENGE = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFG"
_CONTROLLER_ID = "ectrl-0123456789abcdefabcd"
_CONTEXT = "/api/management/v1/context"


def _bootstrap(tmp_path: Path) -> BootstrapSettings:
    return BootstrapSettings(
        mode=BootstrapMode.DEVELOPMENT,
        state_dir=tmp_path / "state",
        runtime_dir=tmp_path / "run",
        control_socket=tmp_path / "run/control.sock",
        ble_service_uuid="179e2e95-b1ee-5aa5-8dcf-7519b6c7ac52",
    )


class _UnusedPort:
    def __getattr__(self, name):
        raise AssertionError(f"unexpected client call: {name}")

    async def close(self) -> None:
        return None


class _ManagementBackend:
    """Stands in for the Admin process that holds the authority credentials."""

    def __init__(self) -> None:
        self.owners_asked: list[str] = []
        self.failure: ManagementBackendError | None = None

    async def context(self, *, owner_id: str) -> dict:
        self.owners_asked.append(owner_id)
        if self.failure is not None:
            raise self.failure
        return {
            "owner_id": owner_id,
            "owner_display_name": "Manson",
            "owner_revision": 3,
            "default_companion_id": "companion-a",
            "capabilities": {name: False for name in _CAPABILITIES},
            "limits": {"max_active_companions": None},
        }

    async def close(self) -> None:
        return None


def _app(tmp_path: Path, backend) -> object:
    unused = _UnusedPort()
    return create_app(
        LocalApiSettings(bootstrap=_bootstrap(tmp_path)),
        workspace_client=unused,  # type: ignore[arg-type]
        runtime_client=unused,  # type: ignore[arg-type]
        devices_client=unused,  # type: ignore[arg-type]
        device_admission_client=unused,  # type: ignore[arg-type]
        host_services_client=unused,  # type: ignore[arg-type]
        management_backend=backend,
    )


def _stub_controller(monkeypatch, *, owner_id: str | None) -> None:
    principal = {
        "contract_version": "1",
        "controller_id": _CONTROLLER_ID,
        "role": "host_admin",
        "reset_epoch": 0,
    }
    if owner_id is not None:
        principal["owner_id"] = owner_id

    async def bootstrap_request(self, operation: str, **_parameters):
        if operation in {"controller.authenticate", "controller.validate"}:
            return principal
        raise AssertionError(f"unexpected bootstrap operation: {operation}")

    monkeypatch.setattr(BootstrapControlClient, "request", bootstrap_request)


async def _authenticate(client: httpx.AsyncClient) -> dict[str, str]:
    session = await client.post(
        "/api/local/v1/auth/sessions",
        json={
            "contract_version": "1",
            "purpose": "eidolon-controller-local-auth-v1",
            "controller_id": _CONTROLLER_ID,
            "challenge": _AUTH_CHALLENGE,
            "reset_epoch": 0,
            "signature": "abcdefgh",
        },
    )
    return {"Authorization": f"Bearer {session.json()['access_token']}"}


async def test_context_answers_for_the_authenticated_owner(tmp_path, monkeypatch) -> None:
    _stub_controller(monkeypatch, owner_id="owner-1")
    backend = _ManagementBackend()
    transport = httpx.ASGITransport(app=_app(tmp_path, backend))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        anonymous = await client.get(_CONTEXT)
        headers = await _authenticate(client)
        answered = await client.get(_CONTEXT, headers=headers)

    assert anonymous.status_code == 401
    # An unauthenticated request never reaches the credential-holding side.
    assert backend.owners_asked == ["owner-1"]

    body = answered.json()
    assert answered.status_code == 200
    assert body["contract_version"] == "1"
    assert body["owner"] == {"owner_id": "owner-1", "display_name": "Manson", "revision": 3}
    assert body["default_companion_id"] == "companion-a"
    # Named once, under ``owner`` — not repeated at the top level.
    assert "owner_id" not in body


async def test_a_caller_cannot_name_a_different_owner(tmp_path, monkeypatch) -> None:
    """The scope rule, asserted against the surface rather than trusted.

    A client that could name an Owner would be asking this boundary to act for
    someone other than whoever it just authenticated. The query string is
    ignored, and what the backend is asked for is the session's Owner.
    """
    _stub_controller(monkeypatch, owner_id="owner-1")
    backend = _ManagementBackend()
    transport = httpx.ASGITransport(app=_app(tmp_path, backend))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        answered = await client.get(f"{_CONTEXT}?owner_id=someone-else", headers=headers)

    assert answered.status_code == 200
    assert answered.json()["owner"]["owner_id"] == "owner-1"
    assert backend.owners_asked == ["owner-1"]


async def test_the_published_contract_declares_no_owner_parameter(tmp_path) -> None:
    """Ignoring a query string is behaviour; declaring nothing is the contract.

    Asserted against the OpenAPI document rather than the router internals,
    because the document is what a generated client is built from: a parameter
    that appears there becomes a parameter two clients can send.
    """
    created = _app(tmp_path, _ManagementBackend())
    operation = created.openapi()["paths"][_CONTEXT]["get"]
    declared = {
        (parameter["in"], parameter["name"])
        for parameter in operation.get("parameters", [])
    }
    assert not any(name == "owner_id" for _location, name in declared)
    assert not any(location == "query" for location, _name in declared)


async def test_a_session_without_an_owner_is_a_conflict_not_an_empty_context(
    tmp_path, monkeypatch
) -> None:
    """A Host whose workspace was never set up has no Owner to answer for.

    Answering with an empty context would let a client render a management
    screen for nobody.
    """
    _stub_controller(monkeypatch, owner_id=None)
    backend = _ManagementBackend()
    transport = httpx.ASGITransport(app=_app(tmp_path, backend))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        answered = await client.get(_CONTEXT, headers=headers)

    assert answered.status_code == 409
    assert backend.owners_asked == []


async def test_a_backend_refusal_is_relayed_not_reinterpreted(tmp_path, monkeypatch) -> None:
    """This side holds no credentials and no authority facts.

    It is therefore in no position to decide what a refusal means, so the status
    is passed along rather than translated into something friendlier.
    """
    _stub_controller(monkeypatch, owner_id="owner-1")
    backend = _ManagementBackend()
    backend.failure = ManagementBackendError("backend is unreachable", status_code=503)
    transport = httpx.ASGITransport(app=_app(tmp_path, backend))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        answered = await client.get(_CONTEXT, headers=headers)

    assert answered.status_code == 503


async def test_every_capability_is_false_and_the_whole_surface_is_declared(
    tmp_path, monkeypatch
) -> None:
    """False keeps a button from appearing before the thing behind it works.

    The whole v1 surface is declared so a client discovers the shape of the API
    rather than the shape of today's build: a name present and false is a
    feature gate, a name absent is a version skew, and those are different
    problems needing different responses.
    """
    _stub_controller(monkeypatch, owner_id="owner-1")
    transport = httpx.ASGITransport(app=_app(tmp_path, _ManagementBackend()))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        body = (await client.get(_CONTEXT, headers=headers)).json()

    # The whole surface is named, whatever today's build can do. This boundary
    # relays the map; which names are true is the application's judgement and is
    # asserted against _ENABLED in
    # test_a_capability_is_true_only_for_a_slice_declared_closed below, where a
    # stub cannot stand in for it.
    assert set(body["capabilities"]) == set(_CAPABILITIES)
    assert not any(body["capabilities"].values()), "this backend said all false"
    for expected in ("companion.create", "memory.export", "task.manage"):
        assert expected in body["capabilities"]


async def test_a_capability_is_true_only_for_a_slice_declared_closed() -> None:
    """Read against the application, not through a stub that hard-codes a map.

    Going through the public boundary would only prove the stub's answer is
    relayed. This asks the code that decides.
    """

    class _Owners:
        async def get_owner(self, owner_id: str):
            return SimpleNamespace(
                owner_id=owner_id,
                display_name="Manson",
                revision=3,
                default_companion_id="companion-a",
            )

    context = await read_context(owner_id="owner-1", owners=_Owners())

    assert set(context.capabilities) == set(_CAPABILITIES)
    # Compared against _ENABLED rather than a hard-coded "all false": closing
    # the next slice is then a one-line edit to that set, while a capability
    # that turned true *without* being added there still fails here.
    assert {name for name, value in context.capabilities.items() if value} == set(_ENABLED)
    assert context.capabilities["companion.read"] is True, "the first closed slice"
    # Named examples on both sides, so the assertion above cannot pass by
    # everything being true or everything being false.
    for closed in (
        "memory.export",
        "task.manage",
        "persona.govern",
        "session.revoke",
        "companion.archive",
        "companion.restore",
        "companion.rename",
    ):
        assert context.capabilities[closed] is True, closed
    for still_open in ("device.manage", "host.operate", "controller.manage"):
        assert context.capabilities[still_open] is False, still_open


async def test_limits_are_null_rather_than_a_number_nobody_measured(
    tmp_path, monkeypatch
) -> None:
    """The plan's "8 active Companions" is a proposal, not a measurement.

    Sending null means the client cannot hard-code it, and the number arrives
    from the Host once a capacity result exists.
    """
    _stub_controller(monkeypatch, owner_id="owner-1")
    transport = httpx.ASGITransport(app=_app(tmp_path, _ManagementBackend()))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        body = (await client.get(_CONTEXT, headers=headers)).json()

    assert body["limits"] == {"max_active_companions": None}
