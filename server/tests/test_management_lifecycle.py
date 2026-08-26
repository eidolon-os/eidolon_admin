"""Putting an Eidolon away, and bringing it back.

This layer is a relay, and these tests hold the two things a relay can still get
wrong: which end state it asks for, and whether a refusal arrives as something a
person can act on.

The rest moved to where it belongs. That "archiving goes through retiring" is
Data's, and it does it in one transaction — an earlier version of this module
walked the graph itself, which bought a window where a dying Host left a
Companion `retiring` and no screen offered a way out. Ownership is Data's too:
the lifecycle route takes the Owner and answers 404 for someone else's, so the
extra read this module used to make was a second place to be wrong about one
fact. Both are asserted in ``eidolon_data``.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from eidolon_admin_server.app.control_plane.contracts import CompanionLifecycleResult
from eidolon_admin_server.app.control_plane.errors import AuthorityFailure
from eidolon_admin_server.app.management.lifecycle import bring_back, put_away
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


def _lifecycle_path(companion_id: str = "companion-a") -> str:
    return f"/api/management/v1/companions/{companion_id}/lifecycle"


class _Bodies:
    """Kernel's Bodies, and letting one go."""

    def __init__(self, *assigned: str) -> None:
        self.endpoints = [
            SimpleNamespace(
                device_id=device_id,
                body_endpoint_id=f"{device_id}:body",
                present=True,
                assignment=SimpleNamespace(
                    companion_id="companion-a",
                    revision=index + 3,
                ),
            )
            for index, device_id in enumerate(assigned)
        ]
        self.released: list[tuple[str, str, int, str]] = []

    async def list_owner_body_endpoints(self, owner_id: str):
        return SimpleNamespace(endpoints=tuple(self.endpoints))

    async def release_device(
        self,
        *,
        owner_id: str,
        device_id: str,
        request_id: str,
        expected_assignment_revision: int,
        change_reason: str,
    ) -> None:
        self.released.append(
            (device_id, request_id, expected_assignment_revision, change_reason)
        )


class _Authority:
    """Data's lifecycle route, remembering exactly what it was asked."""

    def __init__(self, *, refuse: str | None = None) -> None:
        self.calls: list[dict] = []
        self.refuse = refuse

    async def set_companion_lifecycle(
        self,
        owner_id,
        *,
        companion_id,
        lifecycle_state,
        expected_revision=None,
        replacement_companion_id=None,
    ) -> CompanionLifecycleResult:
        self.calls.append(
            {
                "owner_id": owner_id,
                "companion_id": companion_id,
                "lifecycle_state": lifecycle_state,
                "expected_revision": expected_revision,
                "replacement_companion_id": replacement_companion_id,
            }
        )
        if self.refuse is not None:
            raise AuthorityFailure(
                "data", "conflict", "refused", 409, 409, False, self.refuse
            )
        return CompanionLifecycleResult(
            operation="companion.lifecycle",
            companion_id=companion_id,
            lifecycle_state=lifecycle_state,
            revision=5,
            default_companion_id=replacement_companion_id or "companion-b",
        )


# --- the application ------------------------------------------------------


async def test_putting_one_away_asks_for_where_it_should_end_up() -> None:
    """One call stating an end, not a walk. The step in between is Data's, and
    it takes it inside one transaction — so there is no moment where this
    Companion is neither active nor archived."""

    authority = _Authority()
    view = await put_away(
        owner_id="owner-1",
        companion_id="companion-a",
        replacement_companion_id="companion-b",
        expected_revision=4,
        lifecycle=authority,
        bodies=_Bodies(),
    )

    assert authority.calls == [
        {
            "owner_id": "owner-1",
            "companion_id": "companion-a",
            "lifecycle_state": "archived",
            "expected_revision": 4,
            "replacement_companion_id": "companion-b",
        }
    ]
    assert view.lifecycle_state == "archived"
    assert view.default_companion_id == "companion-b"


async def test_bringing_one_back_says_nothing_about_who_answers() -> None:
    """Restoring says "this one is here again". Which Eidolon answers when
    nobody was named is a separate thing the Owner decided, and quietly
    reversing it would undo a decision they made."""

    authority = _Authority()
    view = await bring_back(
        owner_id="owner-1", companion_id="companion-a", lifecycle=authority
    )

    assert [call["lifecycle_state"] for call in authority.calls] == ["active"]
    assert authority.calls[0]["replacement_companion_id"] is None
    assert view.default_companion_id == "companion-b"


async def test_the_host_never_picks_who_answers_instead() -> None:
    """The refusal is relayed, with the word that makes it a question.

    ``default_replacement_required`` is not a failure to smooth over: it is the
    Host saying "this is the one that answers you — who should answer instead?".
    Choosing on the Owner's behalf would be this layer deciding something about
    their life.
    """

    authority = _Authority(refuse="default_replacement_required")
    with pytest.raises(AuthorityFailure) as refused:
        await put_away(
            owner_id="owner-1",
            companion_id="companion-a",
            lifecycle=authority,
            bodies=_Bodies(),
        )

    assert refused.value.code == "default_replacement_required"
    assert authority.calls[0]["replacement_companion_id"] is None


# --- the public boundary ---------------------------------------------------


class _Backend:
    def __init__(self, *, error: ManagementBackendError | None = None) -> None:
        self.asked: list[dict] = []
        self.error = error

    async def set_companion_lifecycle(
        self,
        *,
        owner_id,
        companion_id,
        lifecycle_state,
        replacement_companion_id,
        expected_revision,
    ) -> dict:
        self.asked.append(
            {
                "owner_id": owner_id,
                "companion_id": companion_id,
                "lifecycle_state": lifecycle_state,
                "replacement_companion_id": replacement_companion_id,
                "expected_revision": expected_revision,
            }
        )
        if self.error is not None:
            raise self.error
        return {
            "contract_version": "1",
            "operation": "companion.lifecycle",
            "companion_id": companion_id,
            "lifecycle_state": lifecycle_state,
            "revision": 6,
            "default_companion_id": replacement_companion_id or "companion-b",
        }

    def __getattr__(self, name):
        async def unused(*args, **kwargs):
            raise AssertionError(f"this test never calls {name}")

        return unused


class _Unused:
    def __getattr__(self, name):
        async def unused(*args, **kwargs):
            raise AssertionError(f"nothing here should be called: {name}")

        return unused


def _app(tmp_path: Path, backend):
    unused = _Unused()
    return create_app(
        LocalApiSettings(
            bootstrap=BootstrapSettings(
                mode=BootstrapMode.DEVELOPMENT,
                state_dir=tmp_path / "state",
                runtime_dir=tmp_path / "run",
                control_socket=tmp_path / "run/control.sock",
                ble_service_uuid="179e2e95-b1ee-5aa5-8dcf-7519b6c7ac52",
            )
        ),
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


async def test_the_owner_is_whoever_signed_in_and_is_not_expressible(
    tmp_path, monkeypatch
) -> None:
    _stub_controller(monkeypatch, owner_id="owner-1")
    backend = _Backend()
    transport = httpx.ASGITransport(app=_app(tmp_path, backend))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        anonymous = await client.put(
            _lifecycle_path(), json={"lifecycle_state": "archived"}
        )
        headers = await _authenticate(client)
        answered = await client.put(
            _lifecycle_path(),
            json={"lifecycle_state": "archived", "replacement_companion_id": "companion-b"},
            headers=headers,
        )
        named_someone = await client.put(
            _lifecycle_path(),
            json={"lifecycle_state": "archived", "owner_id": "owner-2"},
            headers=headers,
        )

    assert anonymous.status_code == 401
    assert answered.status_code == 200
    assert answered.json()["lifecycle_state"] == "archived"
    assert answered.json()["default_companion_id"] == "companion-b"
    # An Owner is not a field of this request and never becomes one.
    assert named_someone.status_code == 422
    assert [call["owner_id"] for call in backend.asked] == ["owner-1"]


async def test_only_the_two_states_a_person_asks_for_are_expressible(
    tmp_path, monkeypatch
) -> None:
    """``retiring`` is a step the Host walks; ``deleting`` is another workflow.

    A surface where "put this away" and "erase this forever" are one field apart
    is a surface that eventually erases something.
    """

    _stub_controller(monkeypatch, owner_id="owner-1")
    backend = _Backend()
    transport = httpx.ASGITransport(app=_app(tmp_path, backend))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        refused = [
            (
                await client.put(
                    _lifecycle_path(), json={"lifecycle_state": state}, headers=headers
                )
            ).status_code
            for state in ("retiring", "deleting", "nonsense")
        ]

    assert refused == [422, 422, 422]
    assert backend.asked == []


async def test_a_refusal_that_is_a_question_reaches_the_client_as_one(
    tmp_path, monkeypatch
) -> None:
    """Code and sentence, not a sentence a client would have to match on.

    Before this, every refusal arrived as the same anonymous status, so "name
    someone to answer instead" was indistinguishable from a lost race — and the
    app could only shrug at both.
    """

    _stub_controller(monkeypatch, owner_id="owner-1")
    backend = _Backend(
        error=ManagementBackendError(
                "refused",
                status_code=409,
                refusal=refusal_for_status(409, "refused", "default_replacement_required"),
            )
    )
    transport = httpx.ASGITransport(app=_app(tmp_path, backend))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        answered = await client.put(
            _lifecycle_path(), json={"lifecycle_state": "archived"}, headers=headers
        )

    assert answered.status_code == 409
    assert answered.json()["detail"]["code"] == "default_replacement_required"


async def test_a_refusal_without_a_domain_code_still_says_which_refusal_it_is(
    tmp_path, monkeypatch
) -> None:
    """One shape now, and it carries a kind even when nothing named a code.

    This used to assert the opposite — a bare sentence — on the grounds that a
    refusal is the worst moment to hand an older client something it cannot
    parse. That was right while clients were hand-written. Both are generated
    from this surface's document and regenerated with this change, and the cost
    of the old shape was paid every time somebody had to guess whether 被拒绝
    meant "not configured", "not running" or "you lost a race".
    """

    _stub_controller(monkeypatch, owner_id="owner-1")
    backend = _Backend(error=ManagementBackendError(
                "upstream is away",
                status_code=503,
                refusal=refusal_for_status(503, "upstream is away"),
            ))
    transport = httpx.ASGITransport(app=_app(tmp_path, backend))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        answered = await client.put(
            _lifecycle_path(), json={"lifecycle_state": "active"}, headers=headers
        )

    assert answered.status_code == 503
    assert answered.json()["detail"] == {
        "kind": "upstream",
        "reason": "upstream is away",
        "code": None,
        # Worth waiting out, unlike a Host nobody configured — the one
        # distinction a single sentence could never make.
        "retryable": True,
    }


async def test_putting_one_away_releases_the_devices_that_answered_as_it() -> None:
    """The step §8.4 called Body handling, done where it can be.

    A speaker attached to a Companion that has been put away stops working with
    no explanation: the runtime refuses to start a session for a Companion that
    is not active, which is right, and leaves a person with a mute device and
    nothing on screen. So the mounts go first, and the answer says which.

    Before the state moves, not after — a device pointing at an archived
    Companion is exactly the broken state — and nothing can re-attach behind it,
    because Kernel refuses a Companion that is not active.
    """

    bodies = _Bodies("speaker-living-room", "speaker-study")
    authority = _Authority()

    view = await put_away(
        owner_id="owner-1",
        companion_id="companion-a",
        lifecycle=authority,
        bodies=bodies,
    )

    assert [device for device, _r, _v, _reason in bodies.released] == [
        "speaker-living-room",
        "speaker-study",
    ]
    # The revision each assignment was just read at, so two people archiving at
    # once do not both write it; and a deterministic request id, so a retry
    # replays the same mutation instead of making a second one.
    assert [revision for _d, _r, revision, _reason in bodies.released] == [3, 4]
    assert {request for _d, request, _v, _reason in bodies.released} == {
        "companion-archive-companion-a-speaker-living-room",
        "companion-archive-companion-a-speaker-study",
    }
    # The reason goes on the record, not just into this answer. Both of these
    # leave a Body answering as nobody; only one of them is something the person
    # did to that speaker, and tomorrow the record is all a screen will have.
    assert {reason for _d, _r, _v, reason in bodies.released} == {"companion-archived"}
    assert view.released_devices == ("speaker-living-room", "speaker-study")


async def test_a_device_answering_as_someone_else_is_left_alone() -> None:
    """Only the Bodies that answer as *this* Eidolon.

    A Body nobody has decided about is left alone for the same reason and by a
    different route: it has no assignment row at all, so there is nothing to
    replace and no revision to compare against.
    """

    bodies = _Bodies("speaker-living-room")
    bodies.endpoints[0].assignment.companion_id = "companion-b"
    bodies.endpoints.append(
        SimpleNamespace(
            device_id="speaker-undecided",
            body_endpoint_id="speaker-undecided:body",
            present=True,
            assignment=None,
        )
    )

    view = await put_away(
        owner_id="owner-1",
        companion_id="companion-a",
        lifecycle=_Authority(),
        bodies=bodies,
    )

    assert bodies.released == []
    assert view.released_devices == ()


async def test_a_device_is_not_handed_to_the_successor_who_answers_instead() -> None:
    """Naming who answers unaddressed messages is not a decision about hardware.

    Moving a speaker to a different Eidolon on that inference is the kind of
    helpfulness that makes a system feel out of control. "Answers as nobody" is
    a state this Host already treats as ordinary.
    """

    bodies = _Bodies("speaker-living-room")

    await put_away(
        owner_id="owner-1",
        companion_id="companion-a",
        replacement_companion_id="companion-b",
        lifecycle=_Authority(),
        bodies=bodies,
    )

    assert [device for device, _r, _v, _reason in bodies.released] == [
        "speaker-living-room"
    ]
    assert not hasattr(bodies, "assigned_to"), "nothing was re-assigned anywhere"
