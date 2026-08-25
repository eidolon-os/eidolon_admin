"""What has been done to this Owner's things, as a person reads it.

Written because the lane already existed and had nothing behind it: the App's
「主机动态」 called `/api/local/v1/activity`, which had been deleted — and
meanwhile every governance change Data makes had been writing a fact in the same
transaction that nothing ever read.

What these hold:

- **names, not identifiers.** ``companion.archived c_9f3a`` is not something a
  person can read; the same event beside 小忆 is;
- **one roster read per page**, not one per row, and none at all for a page
  that names no Companion;
- **an unfamiliar action still travels.** A Host newer than its client records
  acts the client has no word for, and a history with holes is worse than a
  history with an unfamiliar line in it;
- **the payload is allow-listed**, because a governance payload is written for
  an audit reader and may grow fields nobody meant to put in front of a person.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from eidolon_admin_server.app.control_plane.contracts import (
    CompanionRosterPage,
    CompanionSummary,
    GovernanceEvent,
    OwnerGovernanceEvents,
)
from eidolon_admin_server.app.management.activity_feed import read_activity
from eidolon_admin_server.bootstrap.config import BootstrapMode, BootstrapSettings
from eidolon_admin_server.bootstrap.control import BootstrapControlClient
from eidolon_admin_server.local_api.app import create_app
from eidolon_admin_server.local_api.config import LocalApiSettings

pytestmark = pytest.mark.asyncio

_AUTH_CHALLENGE = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFG"
_CONTROLLER_ID = "ectrl-0123456789abcdefabcd"
_MADE = datetime(2026, 8, 24, 9, 30, tzinfo=UTC)


def _event(action: str, **overrides) -> GovernanceEvent:
    fields = {
        "event_id": f"audit-{action}",
        "action": action,
        "subject_type": "companion",
        "subject_id": "companion-a",
        "outcome": "success",
        "severity": "info",
        "occurred_at": "2026-08-25T09:00:00+00:00",
        "payload": {},
    }
    fields.update(overrides)
    return GovernanceEvent(**fields)


class _History:
    def __init__(self, *events: GovernanceEvent, next_cursor: int | None = None) -> None:
        self.events = events
        self.next_cursor = next_cursor
        self.asked: list[tuple[str, int | None, int | None]] = []

    async def list_governance_events(self, owner_id, *, limit=None, before=None):
        self.asked.append((owner_id, limit, before))
        return OwnerGovernanceEvents(
            contract_version="1",
            operation="owner.governance-events",
            owner_id=owner_id,
            events=self.events,
            next_cursor=self.next_cursor,
        )


class _Companions:
    def __init__(self) -> None:
        self.reads = 0

    async def list_owner_companions(self, owner_id, *, cursor=None, limit=None):
        self.reads += 1
        return CompanionRosterPage(
            contract_version="1",
            operation="companion.roster-page",
            owner_id=owner_id,
            default_companion_id="companion-b",
            companions=(
                CompanionSummary(
                    companion_id="companion-a",
                    display_name="小忆",
                    kind="conversational",
                    lifecycle_state="archived",
                    revision=4,
                    created_at=_MADE,
                    updated_at=_MADE,
                ),
                CompanionSummary(
                    companion_id="companion-b",
                    display_name="阿力",
                    kind="conversational",
                    lifecycle_state="active",
                    revision=2,
                    created_at=_MADE,
                    updated_at=_MADE,
                ),
            ),
            next_cursor=None,
        )

    async def get_owner_companion(self, owner_id, companion_id):
        raise AssertionError("the feed never reads one Companion")


async def test_an_event_arrives_with_the_name_of_what_it_happened_to() -> None:
    companions = _Companions()
    feed = await read_activity(
        owner_id="owner-1",
        history=_History(
            _event("companion.archived"),
            _event(
                "companion.retirement_begun",
                payload={"replacement_companion_id": "companion-b"},
            ),
        ),
        companions=companions,
    )

    assert [moment.subject_name for moment in feed.moments] == ["小忆", "小忆"]
    # The detail a sentence needs, with its own identifier named too: an id in a
    # detail is as unreadable as one in a subject.
    assert feed.moments[1].detail == {
        "replacement_companion_id": "companion-b",
        "replacement_companion_id_name": "阿力",
    }
    # One read for the page, not one per row.
    assert companions.reads == 1


async def test_a_payload_that_names_a_companion_gets_names_too() -> None:
    """The subject is the obvious place a Companion appears, not the only one.

    "Who answers now changed" is an event about the *Owner* whose whole meaning
    is two Companions named in its payload. Checking only the subject left those
    lines showing identifiers — the one thing this layer exists to prevent.
    """

    companions = _Companions()
    feed = await read_activity(
        owner_id="owner-1",
        history=_History(
            _event(
                "owner.default_companion_changed",
                subject_type="owner",
                subject_id="owner-1",
                payload={"companion_id": "companion-b"},
            )
        ),
        companions=companions,
    )

    assert companions.reads == 1
    assert feed.moments[0].detail["companion_id_name"] == "阿力"


async def test_a_history_that_names_no_companion_costs_no_roster_read() -> None:
    companions = _Companions()
    feed = await read_activity(
        owner_id="owner-1",
        history=_History(
            _event("owner.created", subject_type="owner", subject_id="owner-1")
        ),
        companions=companions,
    )

    assert companions.reads == 0
    assert feed.moments[0].subject_name == ""


async def test_an_action_this_version_never_heard_of_still_happened() -> None:
    """Dropping it would be this layer deciding a person's history for them."""

    feed = await read_activity(
        owner_id="owner-1",
        history=_History(_event("companion.did-something-from-a-later-release")),
        companions=_Companions(),
    )

    assert feed.moments[0].action == "companion.did-something-from-a-later-release"
    assert feed.moments[0].subject_name == "小忆"
    # And nothing was invented for it.
    assert feed.moments[0].detail == {}


async def test_only_allow_listed_payload_fields_reach_a_person() -> None:
    """A governance payload is written for an audit reader.

    It can grow anything — a trace, an internal id, the reason a machine gave
    itself. What a sentence needs is small and known, so it is named rather than
    passed through.
    """

    feed = await read_activity(
        owner_id="owner-1",
        history=_History(
            _event(
                "owner.default_companion_changed",
                subject_type="owner",
                subject_id="owner-1",
                payload={
                    "companion_id": "companion-b",
                    "previous_companion_id": "companion-a",
                    "reason": "retirement",
                    "internal_trace": "not for a person",
                },
            )
        ),
        companions=_Companions(),
    )

    assert feed.moments[0].detail == {
        "companion_id": "companion-b",
        "companion_id_name": "阿力",
        "previous_companion_id": "companion-a",
        "previous_companion_id_name": "小忆",
        "reason": "retirement",
    }


async def test_a_companion_that_no_longer_exists_keeps_its_place() -> None:
    """Deleting an Eidolon does not un-happen what was done to it."""

    feed = await read_activity(
        owner_id="owner-1",
        history=_History(_event("companion.archived", subject_id="companion-gone")),
        companions=_Companions(),
    )

    assert feed.moments[0].subject_id == "companion-gone"
    assert feed.moments[0].subject_name == ""


class _Backend:
    def __init__(self) -> None:
        self.asked: list[dict] = []

    async def activity(self, *, owner_id, limit, before) -> dict:
        self.asked.append({"owner_id": owner_id, "limit": limit, "before": before})
        return {
            "contract_version": "1",
            "operation": "owner.activity",
            "moments": [
                {
                    "event_id": "audit-1",
                    "action": "companion.archived",
                    "subject_type": "companion",
                    "subject_id": "companion-a",
                    "subject_name": "小忆",
                    "occurred_at": "2026-08-25T09:00:00+00:00",
                    "outcome": "success",
                    "detail": {},
                }
            ],
            "next_cursor": 7,
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


def _stub_controller(monkeypatch, *, owner_id: str | None = "owner-1") -> None:
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


async def test_the_history_is_the_signed_in_owners_and_pages_backwards(
    tmp_path, monkeypatch
) -> None:
    _stub_controller(monkeypatch)
    backend = _Backend()
    transport = httpx.ASGITransport(app=_app(tmp_path, backend))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        anonymous = await client.get("/api/management/v1/activity")
        headers = await _authenticate(client)
        first = await client.get("/api/management/v1/activity", headers=headers)
        older = await client.get(
            "/api/management/v1/activity",
            params={"limit": 20, "cursor": "7"},
            headers=headers,
        )
        nonsense = await client.get(
            "/api/management/v1/activity",
            params={"cursor": "somewhere-else"},
            headers=headers,
        )
        named = await client.get(
            "/api/management/v1/activity?owner_id=owner-2", headers=headers
        )

    assert anonymous.status_code == 401
    assert first.status_code == 200
    assert first.json()["moments"][0]["subject_name"] == "小忆"
    # Opaque on the way out, exactly like the roster's: a client stores it and
    # sends it back.
    assert first.json()["next_cursor"] == "7"
    assert older.status_code == 200
    assert named.status_code == 200
    # An Owner is not expressible, and a query string that names one is ignored
    # rather than obeyed.
    # A cursor this surface never issued is refused rather than answered from
    # the top: starting over silently answers a different question.
    assert nonsense.status_code == 422
    assert [call["owner_id"] for call in backend.asked] == ["owner-1"] * 3
    assert backend.asked[1] == {"owner_id": "owner-1", "limit": 20, "before": 7}
