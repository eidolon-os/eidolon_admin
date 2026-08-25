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
from eidolon_admin_server.local_api.management.router import (
    ManagementBackendError,
    refusal_for_status,
)

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
    backend.failure = ManagementBackendError(
                "backend is unreachable",
                status_code=503,
                refusal=refusal_for_status(503, "backend is unreachable"),
            )
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

    context = await read_context(
        owner_id="owner-1", owners=_Owners(), credentials=_Holding("memory", "agent")
    )

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
        "companion.face",
        "controller.manage",
        "host.read",
        "host.operate",
        "activity.read",
    ):
        assert context.capabilities[closed] is True, closed
    for still_open in ("device.read", "device.manage"):
        assert context.capabilities[still_open] is False, still_open


class _Holding:
    """A Host that was given keys to exactly these authorities."""

    def __init__(self, *authorities: str) -> None:
        self._authorities = frozenset(authorities)

    def configured_authorities(self) -> frozenset[str]:
        return self._authorities


async def _capabilities(*held: str) -> dict[str, bool]:
    class _Owners:
        async def get_owner(self, owner_id: str):
            return SimpleNamespace(
                owner_id=owner_id,
                display_name="Manson",
                revision=3,
                default_companion_id="companion-a",
            )

    context = await read_context(
        owner_id="owner-1", owners=_Owners(), credentials=_Holding(*held)
    )
    return context.capabilities


async def test_a_host_without_the_memory_key_does_not_offer_memory() -> None:
    """The state a real Host was in, and the screens it drew anyway.

    Installed before the memory credential existed, it answered
    ``memory.read: true`` — so the phone offered 记忆库, 今天记下的, 导出记忆 and
    让它忘掉, and all four failed on contact with a refusal nobody could read.
    A capability that says "this Host can do this at all" must not be true on a
    Host that was never given the key.
    """

    capabilities = await _capabilities("agent")

    for withdrawn in ("memory.read", "memory.govern", "memory.export"):
        assert capabilities[withdrawn] is False, withdrawn
    # Unrelated slices are untouched: this is a missing key, not a broken Host.
    assert capabilities["companion.read"] is True
    assert capabilities["persona.read"] is True
    assert capabilities["conversation.read"] is True


async def test_a_host_without_the_agent_key_does_not_offer_the_agent_surface() -> None:
    """The other half of the same Host, and the worse half.

    These refusals did not even arrive as refusals: the Agent authority was
    missing from the wire model that serialises them, so every one became a 500.
    """

    capabilities = await _capabilities("memory")

    for withdrawn in ("conversation.read", "task.read", "task.manage", "session.revoke"):
        assert capabilities[withdrawn] is False, withdrawn
    assert capabilities["memory.read"] is True
    assert capabilities["companion.read"] is True


async def test_a_capability_needing_no_authority_is_unaffected_by_keys() -> None:
    """Renaming, faces, the machine itself: Data or this Host's own trust root.

    Asserted so the gate above cannot be satisfied by making everything depend
    on everything, which would turn one missing key into a Host that offers
    nothing.
    """

    capabilities = await _capabilities()

    for independent in (
        "companion.read",
        "companion.create",
        "companion.rename",
        "owner.rename",
        "companion.face",
        "persona.read",
        "persona.govern",
        "controller.manage",
        "host.read",
        "host.operate",
        "activity.read",
    ):
        assert capabilities[independent] is True, independent


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


async def test_a_withheld_capability_says_which_kind_of_withheld_it_is() -> None:
    """Two reasons, because they lead to two different places.

    "This Host's software cannot do it yet" is a release nobody has cut. "This
    Host was never given the key" is a command somebody can run tonight. A
    boolean shows the same shrug for both, and the shrug is what turned a
    missing credential into two weeks of guessing.
    """

    from eidolon_admin_server.app.management.context import (
        WITHHELD_HOST_NOT_CONFIGURED,
        WITHHELD_NOT_BUILT,
    )

    class _Owners:
        async def get_owner(self, owner_id: str):
            return SimpleNamespace(
                owner_id=owner_id,
                display_name="Manson",
                revision=3,
                default_companion_id="companion-a",
            )

    context = await read_context(
        owner_id="owner-1", owners=_Owners(), credentials=_Holding("agent")
    )

    # Closed slice, key missing: fixable here, tonight.
    assert context.unavailable["memory.read"] == WITHHELD_HOST_NOT_CONFIGURED
    assert context.unavailable["memory.govern"] == WITHHELD_HOST_NOT_CONFIGURED
    # Slice not closed: nothing on this Host will change that.
    assert context.unavailable["device.read"] == WITHHELD_NOT_BUILT
    # Available capabilities say nothing, so a client can read the map as
    # "everything in here is off, and this is why".
    assert "companion.read" not in context.unavailable
    assert "conversation.read" not in context.unavailable
    assert set(context.unavailable) == {
        name for name, value in context.capabilities.items() if not value
    }


async def test_the_reason_map_and_the_boolean_map_cannot_disagree() -> None:
    """One computation, two shapes. Two computations would drift."""

    class _Owners:
        async def get_owner(self, owner_id: str):
            return SimpleNamespace(
                owner_id=owner_id,
                display_name="Manson",
                revision=3,
                default_companion_id=None,
            )

    for held in (frozenset(), frozenset({"memory"}), frozenset({"memory", "agent"})):
        context = await read_context(
            owner_id="owner-1", owners=_Owners(), credentials=_Holding(*held)
        )
        for name, available in context.capabilities.items():
            assert available is (name not in context.unavailable), name
