"""Putting an Eidolon away, and bringing it back.

The interesting part is not that a state changes. It is the handful of ways this
particular change goes wrong:

- the two-step route (``active`` → ``retiring`` → ``archived``) written down a
  second time in the projection, so that adding a state breaks one copy;
- the step in between skipped, which is the step where new sessions stop;
- a replacement for the Owner's default *chosen* by the Host rather than asked
  of the person;
- an interrupted archive that cannot be finished by asking again;
- and the refusal that is really a question — "who should answer instead?" —
  arriving at the client as an anonymous 409.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from eidolon_admin_server.app.control_plane.contracts import (
    CompanionIdentity,
    CompanionLifecycleResult,
    OwnerIdentity,
)
from eidolon_admin_server.app.control_plane.errors import AuthorityFailure
from eidolon_admin_server.app.management.lifecycle import bring_back, put_away
from eidolon_admin_server.bootstrap.config import BootstrapMode, BootstrapSettings
from eidolon_admin_server.bootstrap.control import BootstrapControlClient
from eidolon_admin_server.local_api.app import create_app
from eidolon_admin_server.local_api.config import LocalApiSettings
from eidolon_admin_server.local_api.management.router import ManagementBackendError

pytestmark = pytest.mark.asyncio

_AUTH_CHALLENGE = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFG"
_CONTROLLER_ID = "ectrl-0123456789abcdefabcd"


def _lifecycle_path(companion_id: str = "companion-a") -> str:
    return f"/api/management/v1/companions/{companion_id}/lifecycle"


class _Companions:
    def __init__(self, state: str = "active", revision: int = 4) -> None:
        self.state = state
        self.revision = revision

    async def list_owner_companions(self, owner_id, *, cursor=None, limit=None):
        raise AssertionError("lifecycle never lists")

    async def get_owner_companion(self, owner_id, companion_id):
        if companion_id != "companion-a":
            raise AuthorityFailure("data", "not_found", "companion not found", 404)
        return CompanionIdentity(
            operation="companion.identity",
            companion_id=companion_id,
            owner_id=owner_id,
            display_name="小忆",
            lifecycle_state=self.state,
            kind="conversational",
            revision=self.revision,
        )


class _Owners:
    def __init__(self, default_companion_id: str | None = "companion-a") -> None:
        self.default_companion_id = default_companion_id
        self.reads = 0

    async def get_owner(self, owner_id: str) -> OwnerIdentity:
        self.reads += 1
        return OwnerIdentity(
            operation="owner.identity",
            owner_id=owner_id,
            display_name="主人",
            lifecycle_state="active",
            default_companion_id=self.default_companion_id,
            revision=7,
        )


class _Authority:
    """Data's lifecycle route, remembering exactly what it was asked."""

    def __init__(self, *, refuse_at: str | None = None, code: str = "revision_stale") -> None:
        self.calls: list[dict] = []
        self.refuse_at = refuse_at
        self.code = code
        self.revision = 4
        self.default_companion_id: str | None = "companion-a"

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
        if lifecycle_state == self.refuse_at:
            raise AuthorityFailure(
                "data", "conflict", "refused", 409, 409, False, self.code
            )
        self.revision += 1
        if replacement_companion_id:
            self.default_companion_id = replacement_companion_id
        return CompanionLifecycleResult(
            operation="companion.lifecycle",
            companion_id=companion_id,
            lifecycle_state=lifecycle_state,
            revision=self.revision,
            default_companion_id=self.default_companion_id,
        )


# --- the application ------------------------------------------------------


async def test_putting_one_away_goes_through_the_step_where_sessions_stop() -> None:
    """Two calls, in the order the shared transition table says.

    Not because this module knows that order — it asks for the route — but the
    behaviour has to be pinned somewhere, because skipping ``retiring`` would
    archive a Companion that is still being handed runtime snapshots.
    """

    authority = _Authority()
    view = await put_away(
        owner_id="owner-1",
        companion_id="companion-a",
        replacement_companion_id="companion-b",
        companions=_Companions(),
        owners=_Owners(),
        lifecycle=authority,
    )

    assert [call["lifecycle_state"] for call in authority.calls] == ["retiring", "archived"]
    # Only the step that hands the role over carries the replacement. Sending it
    # on both would be relying on the authority to ignore one.
    assert [call["replacement_companion_id"] for call in authority.calls] == [
        "companion-b",
        None,
    ]
    # Each write compares against what the previous one produced, not against a
    # read that is by then a move out of date.
    assert [call["expected_revision"] for call in authority.calls] == [4, 5]
    assert view.lifecycle_state == "archived"
    assert view.default_companion_id == "companion-b"


async def test_asking_for_the_state_it_is_already_in_writes_nothing() -> None:
    """The retry after a lost answer, and the second tap on a stale screen."""

    authority = _Authority()
    owners = _Owners(default_companion_id="companion-b")
    view = await put_away(
        owner_id="owner-1",
        companion_id="companion-a",
        companions=_Companions(state="archived", revision=9),
        owners=owners,
        lifecycle=authority,
    )

    assert authority.calls == []
    assert view.lifecycle_state == "archived"
    assert view.revision == 9
    # Still answers who talks to this person now, so a client parses one shape
    # whether or not anything moved.
    assert view.default_companion_id == "companion-b"
    assert owners.reads == 1


async def test_bringing_one_back_does_not_take_the_default_role_back() -> None:
    """One step, and nothing said about who answers.

    Restoring says "this one is here again". Which Eidolon answers when nobody
    was named is a separate thing the Owner decided, and quietly reversing it
    would undo a decision they made.
    """

    authority = _Authority()
    authority.default_companion_id = "companion-b"
    view = await bring_back(
        owner_id="owner-1",
        companion_id="companion-a",
        companions=_Companions(state="archived"),
        owners=_Owners(),
        lifecycle=authority,
    )

    assert [call["lifecycle_state"] for call in authority.calls] == ["active"]
    assert all(call["replacement_companion_id"] is None for call in authority.calls)
    assert view.default_companion_id == "companion-b"


async def test_an_archive_interrupted_halfway_is_finished_by_asking_again() -> None:
    """Why this workflow needs no journal.

    Each step is an idempotent PUT stating a desired end, and the state in
    between is durable. A Host that dies after the first step leaves a Companion
    ``retiring``; the same request again continues from there rather than
    starting over or refusing.
    """

    failing = _Authority(refuse_at="archived", code="revision_stale")
    with pytest.raises(AuthorityFailure):
        await put_away(
            owner_id="owner-1",
            companion_id="companion-a",
            companions=_Companions(),
            owners=_Owners(),
            lifecycle=failing,
        )
    assert [call["lifecycle_state"] for call in failing.calls] == ["retiring", "archived"]

    # Asked again, against a Companion the first attempt left retiring.
    resumed = _Authority()
    view = await put_away(
        owner_id="owner-1",
        companion_id="companion-a",
        companions=_Companions(state="retiring", revision=5),
        owners=_Owners(),
        lifecycle=resumed,
    )

    assert [call["lifecycle_state"] for call in resumed.calls] == ["archived"]
    assert view.lifecycle_state == "archived"


async def test_the_host_never_picks_who_answers_instead() -> None:
    """The refusal is relayed, with the word that makes it a question.

    ``default_replacement_required`` is not a failure to smooth over: it is the
    Host saying "this is the one that answers you — who should answer instead?".
    Choosing on the Owner's behalf would be this layer deciding something about
    their life.
    """

    authority = _Authority(refuse_at="retiring", code="default_replacement_required")
    with pytest.raises(AuthorityFailure) as refused:
        await put_away(
            owner_id="owner-1",
            companion_id="companion-a",
            companions=_Companions(),
            owners=_Owners(),
            lifecycle=authority,
        )

    assert refused.value.code == "default_replacement_required"
    assert [call["replacement_companion_id"] for call in authority.calls] == [None]


async def test_someone_elses_companion_is_a_404_before_anything_is_written() -> None:
    authority = _Authority()
    with pytest.raises(AuthorityFailure) as refused:
        await put_away(
            owner_id="owner-1",
            companion_id="companion-nobody-elses",
            companions=_Companions(),
            owners=_Owners(),
            lifecycle=authority,
        )

    assert refused.value.status_code == 404
    assert authority.calls == []


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
            "refused", status_code=409, code="default_replacement_required"
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


async def test_a_refusal_without_a_code_still_reads_as_a_sentence(
    tmp_path, monkeypatch
) -> None:
    """The shape every other route on this surface has always had.

    Kept, because a refusal is the worst possible moment to hand an older client
    something it cannot parse.
    """

    _stub_controller(monkeypatch, owner_id="owner-1")
    backend = _Backend(error=ManagementBackendError("upstream is away", status_code=503))
    transport = httpx.ASGITransport(app=_app(tmp_path, backend))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        answered = await client.put(
            _lifecycle_path(), json={"lifecycle_state": "active"}, headers=headers
        )

    assert answered.status_code == 503
    assert answered.json()["detail"] == "upstream is away"
