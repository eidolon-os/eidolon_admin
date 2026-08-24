"""The first read a person actually sees: their Eidolons, listed.

Phase 1's read slice, end to end on the server side — the public boundary, the
loopback adapter, the application read, and the authority client. What the tests
are about is not "a list comes back" but the handful of ways a roster is
normally got wrong:

- a client naming an Owner it was not given;
- "which one is the default" answered in two places, so two rows can claim it;
- a null default quietly resolved by promoting the first row;
- archived Companions hidden, so the roster disagrees with the authority about
  what the Owner has;
- a cursor parsed by something that did not create it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from eidolon_admin_server.app.control_plane.contracts import (
    CompanionRosterPage,
    CompanionSummary,
)
from eidolon_admin_server.app.management.roster import read_roster
from eidolon_admin_server.bootstrap.config import BootstrapMode, BootstrapSettings
from eidolon_admin_server.bootstrap.control import BootstrapControlClient
from eidolon_admin_server.local_api.app import create_app
from eidolon_admin_server.local_api.config import LocalApiSettings
from eidolon_admin_server.local_api.management.router import ManagementBackendError

pytestmark = pytest.mark.asyncio

_AUTH_CHALLENGE = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFG"
_CONTROLLER_ID = "ectrl-0123456789abcdefabcd"
_COMPANIONS = "/api/management/v1/companions"

_MADE = datetime(2026, 8, 24, 9, 30, tzinfo=UTC)


def _row(companion_id: str, *, name: str = "小忆", state: str = "active", kind: str = "standard"):
    return CompanionSummary(
        companion_id=companion_id,
        display_name=name,
        kind=kind,
        lifecycle_state=state,
        revision=2,
        created_at=_MADE,
        updated_at=_MADE,
    )


class _Companions:
    """The authority, answering exactly what it is asked."""

    def __init__(self, page: CompanionRosterPage) -> None:
        self.page = page
        self.calls: list[tuple[str, str | None]] = []

    async def list_owner_companions(self, owner_id, *, cursor=None, limit=None):
        self.calls.append((owner_id, cursor))
        return self.page


def _page(**overrides) -> CompanionRosterPage:
    fields = {
        "contract_version": "1",
        "operation": "companion.roster-page",
        "owner_id": "owner-1",
        "default_companion_id": "companion-a",
        "companions": (_row("companion-a"), _row("companion-b", name="阿力")),
        "next_cursor": None,
    }
    fields.update(overrides)
    return CompanionRosterPage(**fields)


# --- the application read -------------------------------------------------


async def test_the_default_is_named_once_for_the_page() -> None:
    """Not a flag per row. A flag per row makes "both claim it" representable."""
    roster = await read_roster(owner_id="owner-1", companions=_Companions(_page()))

    assert roster.default_companion_id == "companion-a"
    for row in roster.companions:
        assert not hasattr(row, "is_default")


async def test_no_default_stays_no_default() -> None:
    """A real state: every Companion archived, or the only one is a guard.

    Resolving it here by picking a row would put a second answer to "which one
    is the default" in the system, and the two would disagree the moment the
    Owner record changed.
    """
    roster = await read_roster(
        owner_id="owner-1", companions=_Companions(_page(default_companion_id=None))
    )

    assert roster.default_companion_id is None
    assert len(roster.companions) == 2, "and the roster is still shown"


async def test_archived_companions_are_not_hidden() -> None:
    """The Owner archived it; that is a state to show, not a row to drop.

    Filtering here would make this read disagree with the authority about what
    the Owner has, and a person would have no way to see what became of it.
    """
    page = _page(
        companions=(_row("companion-a"), _row("companion-b", state="archived"))
    )
    roster = await read_roster(owner_id="owner-1", companions=_Companions(page))

    assert [row.lifecycle_state for row in roster.companions] == ["active", "archived"]


async def test_the_authority_order_is_kept_as_it_is() -> None:
    """Creation order. An order that put the default first would encode it."""
    page = _page(
        default_companion_id="companion-b",
        companions=(_row("companion-a"), _row("companion-b")),
    )
    roster = await read_roster(owner_id="owner-1", companions=_Companions(page))

    assert [row.companion_id for row in roster.companions] == [
        "companion-a",
        "companion-b",
    ]


async def test_the_cursor_is_carried_both_ways_without_being_read() -> None:
    companions = _Companions(_page(next_cursor="opaque-token"))
    roster = await read_roster(
        owner_id="owner-1", companions=companions, cursor="from-a-previous-page"
    )

    assert companions.calls == [("owner-1", "from-a-previous-page")]
    assert roster.next_cursor == "opaque-token"


async def test_times_are_instants_a_client_can_still_reason_about() -> None:
    roster = await read_roster(owner_id="owner-1", companions=_Companions(_page()))

    assert roster.companions[0].created_at == "2026-08-24T09:30:00+00:00"


# --- the public boundary --------------------------------------------------


class _Backend:
    def __init__(self) -> None:
        self.asked: list[tuple[str, str | None]] = []

    async def context(self, *, owner_id: str) -> dict:
        raise AssertionError("this test never reads context")

    async def roster(self, *, owner_id: str, cursor: str | None) -> dict:
        self.asked.append((owner_id, cursor))
        return {
            "contract_version": "1",
            "operation": "companion.roster",
            "owner_id": owner_id,
            "default_companion_id": "companion-a",
            "companions": [
                {
                    "companion_id": "companion-a",
                    "display_name": "小忆",
                    "kind": "standard",
                    "lifecycle_state": "active",
                    "revision": 2,
                    "created_at": "2026-08-24T09:30:00+00:00",
                    "updated_at": "2026-08-24T09:30:00+00:00",
                }
            ],
            "next_cursor": "next-page",
        }

    async def companion(self, *, owner_id: str, companion_id: str) -> dict:
        self.asked.append((owner_id, companion_id))
        if companion_id != "companion-a":
            raise ManagementBackendError("not found", status_code=404)
        return {
            "contract_version": "1",
            "operation": "companion.detail",
            "companion_id": companion_id,
            "display_name": "小忆",
            "kind": "standard",
            "lifecycle_state": "active",
            "revision": 2,
            "is_default": True,
        }

    async def set_default_companion(
        self, *, owner_id: str, companion_id: str, expected_revision: int
    ) -> dict:
        self.asked.append((owner_id, companion_id, expected_revision))
        if expected_revision != 3:
            # What the authority answers a stale caller. Relayed, not softened:
            # a client must re-read rather than retry.
            raise ManagementBackendError("owner revision moved", status_code=409)
        return {
            "contract_version": "1",
            "operation": "owner.default-companion",
            "default_companion_id": companion_id,
        }

    async def close(self) -> None:
        return None


class _Unused:
    def __getattr__(self, name):
        raise AssertionError(f"unexpected client call: {name}")

    async def close(self) -> None:
        return None


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


async def test_the_roster_is_the_authenticated_owners(tmp_path, monkeypatch) -> None:
    _stub_controller(monkeypatch, owner_id="owner-1")
    backend = _Backend()
    transport = httpx.ASGITransport(app=_app(tmp_path, backend))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        anonymous = await client.get(_COMPANIONS)
        headers = await _authenticate(client)
        answered = await client.get(_COMPANIONS, headers=headers)

    assert anonymous.status_code == 401
    # An unauthenticated request never reaches the credential-holding side.
    assert backend.asked == [("owner-1", None)]
    assert answered.status_code == 200
    body = answered.json()
    assert body["default_companion_id"] == "companion-a"
    assert body["companions"][0]["display_name"] == "小忆"
    assert body["next_cursor"] == "next-page"


async def test_the_public_response_does_not_name_an_owner(tmp_path, monkeypatch) -> None:
    """It would be a second place to read something the session already fixed.

    A client that could read an Owner here would eventually compare it with one
    from somewhere else, and then something has to adjudicate.
    """
    _stub_controller(monkeypatch, owner_id="owner-1")
    transport = httpx.ASGITransport(app=_app(tmp_path, _Backend()))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        body = (await client.get(_COMPANIONS, headers=headers)).json()

    assert "owner_id" not in body
    assert all("owner_id" not in row for row in body["companions"])


async def test_an_owner_cannot_be_asked_for(tmp_path, monkeypatch) -> None:
    """Sending one changes nothing: the parameter does not exist."""
    _stub_controller(monkeypatch, owner_id="owner-1")
    backend = _Backend()
    transport = httpx.ASGITransport(app=_app(tmp_path, backend))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        answered = await client.get(
            _COMPANIONS, params={"owner_id": "owner-2"}, headers=headers
        )

    assert answered.status_code == 200
    assert backend.asked == [("owner-1", None)], "the session's Owner, not the query's"


async def test_the_cursor_a_client_sends_reaches_the_authority(tmp_path, monkeypatch) -> None:
    _stub_controller(monkeypatch, owner_id="owner-1")
    backend = _Backend()
    transport = httpx.ASGITransport(app=_app(tmp_path, backend))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        await client.get(_COMPANIONS, params={"cursor": "page-2"}, headers=headers)

    assert backend.asked == [("owner-1", "page-2")]


async def test_a_session_with_no_owner_is_a_conflict_not_an_empty_list(
    tmp_path, monkeypatch
) -> None:
    """An unprovisioned Host has no Owner to list for.

    Answering with an empty roster would look identical to "you have no
    Eidolons", which is a different thing and would send a person looking for a
    create button that cannot work yet.
    """
    _stub_controller(monkeypatch, owner_id=None)
    backend = _Backend()
    transport = httpx.ASGITransport(app=_app(tmp_path, backend))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        answered = await client.get(_COMPANIONS, headers=headers)

    assert answered.status_code == 409
    assert backend.asked == []


# --- one Eidolon, opened ---------------------------------------------------


async def test_opening_one_asks_for_it_under_the_session_owner(
    tmp_path, monkeypatch
) -> None:
    _stub_controller(monkeypatch, owner_id="owner-1")
    backend = _Backend()
    transport = httpx.ASGITransport(app=_app(tmp_path, backend))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        answered = await client.get(
            "/api/management/v1/companions/companion-a", headers=headers
        )

    assert answered.status_code == 200
    assert backend.asked == [("owner-1", "companion-a")]
    body = answered.json()
    assert body["is_default"] is True
    # Read now so a client about to rename or archive need not fetch again.
    assert body["revision"] == 2
    assert "owner_id" not in body


async def test_someone_elses_companion_is_absent_rather_than_forbidden(
    tmp_path, monkeypatch
) -> None:
    """403 would confirm the id exists. 404 says nothing either way."""
    _stub_controller(monkeypatch, owner_id="owner-1")
    transport = httpx.ASGITransport(app=_app(tmp_path, _Backend()))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        answered = await client.get(
            "/api/management/v1/companions/someone-elses", headers=headers
        )

    assert answered.status_code == 404


async def test_the_default_flag_belongs_to_the_single_answer_only() -> None:
    """The asymmetry is the design, so it is asserted rather than left to notice.

    One Companion carries ``is_default`` because the Host just compared it
    against the Owner's one pointer. A list carries the pointer instead, once,
    because a per-row flag can contradict itself and a single comparison
    cannot.
    """
    from eidolon_admin_server.local_api.management.router import (
        CompanionDetailView,
        CompanionSummaryView,
    )

    assert "is_default" in CompanionDetailView.model_fields
    assert "is_default" not in CompanionSummaryView.model_fields
    assert "default_companion_id" not in CompanionDetailView.model_fields


# --- making one the default ------------------------------------------------

_DEFAULT = "/api/management/v1/owner/default-companion"


async def test_the_switch_is_made_for_the_session_owner(tmp_path, monkeypatch) -> None:
    _stub_controller(monkeypatch, owner_id="owner-1")
    backend = _Backend()
    transport = httpx.ASGITransport(app=_app(tmp_path, backend))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        anonymous = await client.put(
            _DEFAULT, json={"companion_id": "companion-b", "expected_revision": 3}
        )
        headers = await _authenticate(client)
        answered = await client.put(
            _DEFAULT,
            headers=headers,
            json={"companion_id": "companion-b", "expected_revision": 3},
        )

    assert anonymous.status_code == 401
    # An unauthenticated write never reaches the credential-holding side.
    assert backend.asked == [("owner-1", "companion-b", 3)]
    assert answered.status_code == 200
    assert answered.json()["default_companion_id"] == "companion-b"


async def test_the_answer_is_where_the_pointer_now_is_not_what_was_asked(
    tmp_path, monkeypatch
) -> None:
    """Echoed from the authority, so a client shows what happened.

    A client that painted its own choice would be showing its request back to
    the person, which looks identical whether or not the Host agreed.
    """
    _stub_controller(monkeypatch, owner_id="owner-1")
    transport = httpx.ASGITransport(app=_app(tmp_path, _Backend()))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        body = (
            await client.put(
                _DEFAULT,
                headers=headers,
                json={"companion_id": "companion-b", "expected_revision": 3},
            )
        ).json()

    assert set(body) == {"contract_version", "default_companion_id"}


async def test_a_stale_revision_arrives_as_a_conflict(tmp_path, monkeypatch) -> None:
    """409 has to survive the trip intact.

    It is the one refusal a client must answer by re-reading rather than
    retrying, so flattening it into 503 (or into a generic failure) would turn a
    "someone else changed this" into "try again", and the second phone would
    win by persistence.
    """
    _stub_controller(monkeypatch, owner_id="owner-1")
    transport = httpx.ASGITransport(app=_app(tmp_path, _Backend()))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        answered = await client.put(
            _DEFAULT,
            headers=headers,
            json={"companion_id": "companion-b", "expected_revision": 1},
        )

    assert answered.status_code == 409


async def test_a_write_without_a_revision_is_refused(tmp_path, monkeypatch) -> None:
    """Not defaulted to "whatever is current" — that is a blind write.

    A client that has not read the Owner has no business changing this, and
    letting the field be omitted would make the compare-and-swap optional in
    practice while looking mandatory in the contract.
    """
    _stub_controller(monkeypatch, owner_id="owner-1")
    backend = _Backend()
    transport = httpx.ASGITransport(app=_app(tmp_path, backend))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        answered = await client.put(
            _DEFAULT, headers=headers, json={"companion_id": "companion-b"}
        )

    assert answered.status_code == 422
    assert backend.asked == []


async def test_no_owner_may_be_named_in_the_write(tmp_path, monkeypatch) -> None:
    _stub_controller(monkeypatch, owner_id="owner-1")
    backend = _Backend()
    transport = httpx.ASGITransport(app=_app(tmp_path, backend))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        answered = await client.put(
            _DEFAULT,
            headers=headers,
            json={
                "companion_id": "companion-b",
                "expected_revision": 3,
                "owner_id": "owner-2",
            },
        )

    # Refused rather than ignored: a caller that believes it chose the Owner
    # must find out that it did not.
    assert answered.status_code == 422
    assert backend.asked == []
