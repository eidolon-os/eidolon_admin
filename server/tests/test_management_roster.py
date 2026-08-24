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
        self.current = "g_2"

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

    async def create_companion(
        self, *, owner_id: str, operation_id: str, display_name: str, kind: str
    ) -> dict:
        self.asked.append((owner_id, operation_id, display_name, kind))
        already = self.asked.count((owner_id, operation_id, display_name, kind)) > 1
        return {
            "contract_version": "1",
            "operation": "companion.created",
            "companion_id": f"cp-{operation_id[:8]}",
            "display_name": display_name,
            "kind": kind,
            "lifecycle_state": "active",
            "revision": 1,
            "created": not already,
            "memory_ready": True,
        }

    async def memory_library(self, *, owner_id: str, companion_id: str | None) -> dict:
        self.asked.append((owner_id, companion_id))
        return {
            "contract_version": "1",
            "operation": "memory.library",
            "wings": [
                {
                    "wing_id": "Wing_Life",
                    "display_name": "生活",
                    "description": "",
                    "entry_count": 2,
                    "rooms": [
                        {
                            "room_id": "饮食",
                            "entry_count": 2,
                            "titles": ["乌龙茶"],
                            "more": True,
                        }
                    ],
                }
            ],
            "entry_count": 2,
            "withheld_count": 1,
            "truncated": False,
        }

    async def memory_entries(
        self,
        *,
        owner_id: str,
        since: str,
        limit: int | None,
        companion_id: str | None,
    ) -> dict:
        self.asked.append((owner_id, since, limit, companion_id))
        return {
            "contract_version": "1",
            "operation": "memory.day",
            "since": since,
            "entries": [
                {
                    "entry_id": "drawer_1",
                    "recorded_at": "2026-08-24T12:00:00+00:00",
                    "recorded_at_source": "occurred_at",
                    "wing_id": "Wing_Life",
                    "room_id": "饮食",
                    "preview": "乌龙茶",
                }
            ],
            "entry_count": 1,
            "more_in_window": True,
            "undated_count": 2,
            "truncated": False,
        }

    async def assign_memory_audience(
        self, *, owner_id: str, entry_id: str, companion_id: str
    ) -> dict:
        self.asked.append((owner_id, entry_id, companion_id))
        if entry_id == "drawer_gone":
            raise ManagementBackendError("no such memory", status_code=404)
        return {
            "contract_version": "1",
            "operation": "memory.audience",
            "entry_id": entry_id,
            "companion_id": companion_id,
            "status": "applied" if companion_id else "accepted",
        }

    async def persona_history(self, *, owner_id: str, companion_id: str) -> dict:
        self.asked.append((owner_id, companion_id))
        if companion_id == "companion-elsewhere":
            raise ManagementBackendError("not found", status_code=404)
        return self._history()

    async def restore_persona(
        self, *, owner_id: str, companion_id: str, chapter_id: str
    ) -> dict:
        self.asked.append((owner_id, companion_id, chapter_id))
        if companion_id == "companion-elsewhere":
            raise ManagementBackendError("not found", status_code=404)
        if chapter_id == "g_never":
            raise ManagementBackendError(
                "only a committed persona genome can be restored", status_code=409
            )
        self.current = chapter_id
        return self._history()

    def _history(self) -> dict:
        return {
            "contract_version": "1",
            "operation": "companion.persona-history",
            "companion_id": "companion-a",
            "chapters": [
                {
                    "chapter_id": "g_2",
                    "changed_at": "2026-08-20T09:00:00+00:00",
                    "what_changed": "话变少了一点",
                    "restored_from": None,
                    "is_current": self.current == "g_2",
                },
                {
                    "chapter_id": "g_1",
                    "changed_at": "2026-08-01T09:00:00+00:00",
                    "what_changed": "",
                    "restored_from": None,
                    "is_current": self.current == "g_1",
                },
            ],
        }

    async def recollections(
        self, *, owner_id: str, query: str, limit: int, companion_id: str | None
    ) -> dict:
        self.asked.append((owner_id, query, limit, companion_id))
        return {
            "contract_version": "1",
            "operation": "memory.recollections",
            "query": query,
            "recollections": [
                {"text": "他喜欢在下午散步", "remembered_at": "2026-08-16T09:30:00Z"},
                {"text": "没有时间的那一条", "remembered_at": None},
            ],
        }

    async def memory_export(self, *, owner_id: str, companion_id: str | None) -> dict:
        self.asked.append((owner_id, companion_id))
        return {
            "contract_version": "1",
            "operation": "memory.copy",
            "taken_at": "2026-08-24T12:31:00+00:00",
            "records": [
                {
                    "entry_id": "drawer_1",
                    "recorded_at": "2026-08-24T12:00:00+00:00",
                    "recorded_at_source": "occurred_at",
                    "wing_id": "Wing_Life",
                    "room_id": "饮食",
                    "memory_type": "preference",
                    "value": "他喜欢喝乌龙茶。" * 20,
                },
                {
                    "entry_id": "drawer_undated",
                    "recorded_at": "",
                    "recorded_at_source": "",
                    "wing_id": "",
                    "room_id": "",
                    "memory_type": "",
                    "value": "说不清什么时候",
                },
            ],
            "record_count": 2,
            "undated_count": 1,
            "truncated": True,
        }

    async def forget_preview(self, *, owner_id: str, target: str, action: str) -> dict:
        self.asked.append((owner_id, target, action))
        if target == "一切":
            return {
                "contract_version": "1",
                "operation": "memory.forget-proposal",
                "status": "too_broad",
                "target": target,
                "action": None,
                "entries": [],
                "needs_confirmation": False,
                "confirmation_token": None,
                "expires_at": None,
                "detail": "too many matches",
            }
        return {
            "contract_version": "1",
            "operation": "memory.forget-proposal",
            "status": "preview",
            "target": target,
            "action": action,
            "entries": [
                {"entry_id": "drawer_1", "preview": "上周那件事", "score": 0.8}
            ],
            "needs_confirmation": True,
            "confirmation_token": "opaque-token",
            "expires_at": 1900000000,
            "detail": "",
        }

    async def forget_confirm(self, *, owner_id: str, confirmation_token: str) -> dict:
        self.asked.append((owner_id, confirmation_token))
        return {
            "contract_version": "1",
            "operation": "memory.forgotten",
            "action": "delete",
            "target": "上周那件事",
            "entry_count": 1,
            "status": "accepted",
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


# --- adding one ------------------------------------------------------------

_COMPANIONS_WRITE = "/api/management/v1/companions"
_OPERATION = "32c421a3-e0df-40f9-8f75-68745ae39d81"


async def test_adding_one_is_for_the_session_owner(tmp_path, monkeypatch) -> None:
    _stub_controller(monkeypatch, owner_id="owner-1")
    backend = _Backend()
    transport = httpx.ASGITransport(app=_app(tmp_path, backend))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        anonymous = await client.put(
            _COMPANIONS_WRITE,
            json={"operation_id": _OPERATION, "display_name": "阿力"},
        )
        headers = await _authenticate(client)
        answered = await client.put(
            _COMPANIONS_WRITE,
            headers=headers,
            json={"operation_id": _OPERATION, "display_name": "阿力"},
        )

    assert anonymous.status_code == 401
    assert backend.asked == [("owner-1", _OPERATION, "阿力", "conversational")]
    assert answered.status_code == 200
    assert answered.json()["created"] is True
    assert answered.json()["memory_ready"] is True


async def test_asking_twice_with_one_operation_id_creates_one(
    tmp_path, monkeypatch
) -> None:
    """The property PUT exists for: a lost answer is asked for again.

    The second response says ``created: false`` — the Eidolon exists either way,
    and a client that showed "created!" twice for one intent would be telling
    the person something that did not happen.
    """
    _stub_controller(monkeypatch, owner_id="owner-1")
    backend = _Backend()
    transport = httpx.ASGITransport(app=_app(tmp_path, backend))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        body = {"operation_id": _OPERATION, "display_name": "阿力"}
        first = await client.put(_COMPANIONS_WRITE, headers=headers, json=body)
        second = await client.put(_COMPANIONS_WRITE, headers=headers, json=body)

    assert first.json()["companion_id"] == second.json()["companion_id"]
    assert (first.json()["created"], second.json()["created"]) == (True, False)


async def test_an_operation_id_is_required(tmp_path, monkeypatch) -> None:
    """Not generated here.

    A Host that made one up would make every retry a new operation, so the
    protection would exist in the contract and not in fact.
    """
    _stub_controller(monkeypatch, owner_id="owner-1")
    backend = _Backend()
    transport = httpx.ASGITransport(app=_app(tmp_path, backend))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        answered = await client.put(
            _COMPANIONS_WRITE, headers=headers, json={"display_name": "阿力"}
        )

    assert answered.status_code == 422
    assert backend.asked == []


async def test_no_owner_may_be_named_when_adding(tmp_path, monkeypatch) -> None:
    _stub_controller(monkeypatch, owner_id="owner-1")
    backend = _Backend()
    transport = httpx.ASGITransport(app=_app(tmp_path, backend))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        answered = await client.put(
            _COMPANIONS_WRITE,
            headers=headers,
            json={
                "operation_id": _OPERATION,
                "display_name": "阿力",
                "owner_id": "owner-2",
            },
        )

    assert answered.status_code == 422
    assert backend.asked == []


async def test_the_ordinary_case_needs_no_kind(tmp_path, monkeypatch) -> None:
    """A person adding an Eidolon should not have to know guards exist."""
    _stub_controller(monkeypatch, owner_id="owner-1")
    backend = _Backend()
    transport = httpx.ASGITransport(app=_app(tmp_path, backend))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        await client.put(
            _COMPANIONS_WRITE,
            headers=headers,
            json={"operation_id": _OPERATION, "display_name": "阿力"},
        )

    assert backend.asked[0][3] == "conversational"


# --- what my Eidolon remembers --------------------------------------------

_LIBRARY = "/api/management/v1/memory/library"


async def test_the_library_is_the_authenticated_owners(tmp_path, monkeypatch) -> None:
    _stub_controller(monkeypatch, owner_id="owner-1")
    backend = _Backend()
    transport = httpx.ASGITransport(app=_app(tmp_path, backend))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        anonymous = await client.get(_LIBRARY)
        headers = await _authenticate(client)
        answered = await client.get(_LIBRARY, headers=headers)

    assert anonymous.status_code == 401
    assert backend.asked == [("owner-1", None)]
    body = answered.json()
    assert body["entry_count"] == 2
    assert body["wings"][0]["rooms"][0]["titles"] == ["乌龙茶"]


async def test_the_withheld_count_reaches_the_client(tmp_path, monkeypatch) -> None:
    """A silence here would look like a bug in the person's own memory.

    The total and the listed entries differ on purpose — things marked "do not
    bring this up" are counted, not erased — so the number that explains the
    difference has to survive the trip.
    """
    _stub_controller(monkeypatch, owner_id="owner-1")
    transport = httpx.ASGITransport(app=_app(tmp_path, _Backend()))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        body = (await client.get(_LIBRARY, headers=headers)).json()

    assert body["withheld_count"] == 1
    assert body["truncated"] is False


async def test_naming_a_companion_selects_an_audience(tmp_path, monkeypatch) -> None:
    """Not a scope. The memory is the Owner's; naming one adds a layer."""
    _stub_controller(monkeypatch, owner_id="owner-1")
    backend = _Backend()
    transport = httpx.ASGITransport(app=_app(tmp_path, backend))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        await client.get(_LIBRARY, params={"companion_id": "c-a"}, headers=headers)

    assert backend.asked == [("owner-1", "c-a")]


async def test_the_library_names_no_owner_and_no_space(tmp_path, monkeypatch) -> None:
    """The memory space id is an identifier for a thing nobody can open.

    It reaches Admin from the realm and stops there; a client that could see it
    would eventually show it, and it means nothing to a person.
    """
    _stub_controller(monkeypatch, owner_id="owner-1")
    transport = httpx.ASGITransport(app=_app(tmp_path, _Backend()))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        body = (await client.get(_LIBRARY, headers=headers)).json()

    assert "owner_id" not in body
    assert "memory_space_id" not in body


# --- forgetting something -------------------------------------------------

_FORGET_PREVIEW = "/api/management/v1/memory/forget/preview"
_FORGET_CONFIRM = "/api/management/v1/memory/forget/confirm"


async def test_a_preview_is_for_the_session_owner_and_changes_nothing(
    tmp_path, monkeypatch
) -> None:
    _stub_controller(monkeypatch, owner_id="owner-1")
    backend = _Backend()
    transport = httpx.ASGITransport(app=_app(tmp_path, backend))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        anonymous = await client.post(_FORGET_PREVIEW, json={"target": "上周那件事"})
        headers = await _authenticate(client)
        answered = await client.post(
            _FORGET_PREVIEW, headers=headers, json={"target": "上周那件事"}
        )

    assert anonymous.status_code == 401
    assert backend.asked == [("owner-1", "上周那件事", "delete")]
    body = answered.json()
    assert body["status"] == "preview"
    assert body["entries"][0]["entry_id"] == "drawer_1"
    assert body["needs_confirmation"] is True


async def test_the_token_reaches_the_confirm_untouched(tmp_path, monkeypatch) -> None:
    """No layer above the realm may read or rebuild it.

    It is what ties the decision to the entries the person looked at; a client
    or a boundary that could interpret one could confirm something nobody saw.
    """
    _stub_controller(monkeypatch, owner_id="owner-1")
    backend = _Backend()
    transport = httpx.ASGITransport(app=_app(tmp_path, backend))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        preview = (
            await client.post(
                _FORGET_PREVIEW, headers=headers, json={"target": "上周那件事"}
            )
        ).json()
        confirmed = await client.post(
            _FORGET_CONFIRM,
            headers=headers,
            json={"confirmation_token": preview["confirmation_token"]},
        )

    assert backend.asked[-1] == ("owner-1", "opaque-token")
    assert confirmed.json()["entry_count"] == 1
    # The Host's word for where the change got to, relayed rather than read as
    # "done": publishing is durable, applying is a projection still running.
    assert confirmed.json()["status"] == "accepted"


async def test_too_broad_is_not_flattened_into_an_empty_list(
    tmp_path, monkeypatch
) -> None:
    """"You never told me that" and "say which one" are different sentences."""
    _stub_controller(monkeypatch, owner_id="owner-1")
    transport = httpx.ASGITransport(app=_app(tmp_path, _Backend()))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        body = (
            await client.post(_FORGET_PREVIEW, headers=headers, json={"target": "一切"})
        ).json()

    assert body["status"] == "too_broad"
    assert body["entries"] == []
    assert body["confirmation_token"] is None
    assert body["detail"]


async def test_an_action_outside_the_contract_is_refused(tmp_path, monkeypatch) -> None:
    _stub_controller(monkeypatch, owner_id="owner-1")
    backend = _Backend()
    transport = httpx.ASGITransport(app=_app(tmp_path, backend))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        answered = await client.post(
            _FORGET_PREVIEW,
            headers=headers,
            json={"target": "x", "action": "obliterate"},
        )

    assert answered.status_code == 422
    assert backend.asked == []


async def test_no_owner_may_be_named_when_forgetting(tmp_path, monkeypatch) -> None:
    _stub_controller(monkeypatch, owner_id="owner-1")
    backend = _Backend()
    transport = httpx.ASGITransport(app=_app(tmp_path, backend))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        answered = await client.post(
            _FORGET_PREVIEW,
            headers=headers,
            json={"target": "x", "owner_id": "owner-2"},
        )

    assert answered.status_code == 422
    assert backend.asked == []


# --- what it wrote down today ---------------------------------------------

_ENTRIES = "/api/management/v1/memory/entries"
_NOON = "2026-08-24T12:00:00+00:00"


async def test_the_day_is_the_authenticated_owners(tmp_path, monkeypatch) -> None:
    _stub_controller(monkeypatch, owner_id="owner-1")
    backend = _Backend()
    transport = httpx.ASGITransport(app=_app(tmp_path, backend))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        anonymous = await client.get(_ENTRIES, params={"since": _NOON})
        headers = await _authenticate(client)
        answered = await client.get(_ENTRIES, params={"since": _NOON}, headers=headers)

    assert anonymous.status_code == 401
    assert backend.asked == [("owner-1", _NOON, None, None)]
    body = answered.json()
    assert body["since"] == _NOON
    assert body["entries"][0]["preview"] == "乌龙茶"


async def test_the_client_says_when_the_day_started(tmp_path, monkeypatch) -> None:
    """No default, at any layer.

    A day depends on where the person is, and no layer on the Host knows that;
    a default would answer for the wrong day without saying so.
    """
    _stub_controller(monkeypatch, owner_id="owner-1")
    backend = _Backend()
    transport = httpx.ASGITransport(app=_app(tmp_path, backend))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        answered = await client.get(_ENTRIES, headers=headers)

    assert answered.status_code == 422
    assert backend.asked == []


async def test_the_two_partial_answers_stay_apart(tmp_path, monkeypatch) -> None:
    """``more_in_window`` is this page; ``truncated`` is the palace.

    Collapsing them would leave a client unable to say whether asking again
    would help.
    """
    _stub_controller(monkeypatch, owner_id="owner-1")
    transport = httpx.ASGITransport(app=_app(tmp_path, _Backend()))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        body = (
            await client.get(_ENTRIES, params={"since": _NOON}, headers=headers)
        ).json()

    assert body["more_in_window"] is True
    assert body["truncated"] is False
    # And entries with no usable time are a number rather than a silence.
    assert body["undated_count"] == 2


async def test_the_window_and_audience_reach_the_backend(tmp_path, monkeypatch) -> None:
    _stub_controller(monkeypatch, owner_id="owner-1")
    backend = _Backend()
    transport = httpx.ASGITransport(app=_app(tmp_path, backend))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        await client.get(
            _ENTRIES,
            params={"since": _NOON, "limit": 5, "companion_id": "c-a"},
            headers=headers,
        )

    assert backend.asked == [("owner-1", _NOON, 5, "c-a")]


async def test_the_day_names_no_owner_and_no_space(tmp_path, monkeypatch) -> None:
    _stub_controller(monkeypatch, owner_id="owner-1")
    transport = httpx.ASGITransport(app=_app(tmp_path, _Backend()))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        body = (
            await client.get(_ENTRIES, params={"since": _NOON}, headers=headers)
        ).json()

    assert "owner_id" not in body
    assert "memory_space_id" not in body


# --- the copy I keep --------------------------------------------------------

_EXPORT = "/api/management/v1/memory/export"


async def test_the_copy_is_the_authenticated_owners_and_arrives_whole(
    tmp_path, monkeypatch
) -> None:
    """The one read here that shortens nothing.

    The library and the day list are pages someone scrolls; this is a file they
    keep, and a preview in it would be data loss that looks like a working read.
    """
    _stub_controller(monkeypatch, owner_id="owner-1")
    backend = _Backend()
    transport = httpx.ASGITransport(app=_app(tmp_path, backend))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        anonymous = await client.get(_EXPORT)
        headers = await _authenticate(client)
        answered = await client.get(_EXPORT, headers=headers)

    assert anonymous.status_code == 401
    assert backend.asked == [("owner-1", None)]
    body = answered.json()
    assert body["records"][0]["value"] == "他喜欢喝乌龙茶。" * 20
    assert body["record_count"] == 2


async def test_the_copy_says_what_it_could_not_date_and_where_it_stopped(
    tmp_path, monkeypatch
) -> None:
    """Its two honesty counts, which are the whole reason it can be trusted."""

    _stub_controller(monkeypatch, owner_id="owner-1")
    transport = httpx.ASGITransport(app=_app(tmp_path, _Backend()))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        body = (await client.get(_EXPORT, headers=headers)).json()

    assert body["undated_count"] == 1
    assert body["truncated"] is True
    # And the undated one is in the file rather than left out of it.
    assert body["records"][-1]["entry_id"] == "drawer_undated"
    assert body["records"][-1]["recorded_at"] == ""


async def test_the_copy_names_no_owner_and_answers_for_no_other_one(
    tmp_path, monkeypatch
) -> None:
    """The subject comes from the session and nowhere else.

    A named ``owner_id`` is not refused — an unread query parameter on a GET
    never reaches a handler — so what is asserted is the thing that matters: it
    changes nothing. The structural half is elsewhere: the contract gate refuses
    a route that *declares* a parameter naming a subject, which is what would be
    needed for this to be more than noise.
    """

    _stub_controller(monkeypatch, owner_id="owner-1")
    backend = _Backend()
    transport = httpx.ASGITransport(app=_app(tmp_path, backend))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        await client.get(_EXPORT, params={"owner_id": "owner-2"}, headers=headers)
        body = (await client.get(_EXPORT, headers=headers)).json()

    assert backend.asked == [("owner-1", None), ("owner-1", None)]
    assert "owner_id" not in body
    assert "memory_space_id" not in body


async def test_a_copy_may_be_asked_for_one_companions_audience(
    tmp_path, monkeypatch
) -> None:
    """Exactly as the library is, so an export cannot see more than a browse."""

    _stub_controller(monkeypatch, owner_id="owner-1")
    backend = _Backend()
    transport = httpx.ASGITransport(app=_app(tmp_path, backend))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        await client.get(_EXPORT, params={"companion_id": "c-a"}, headers=headers)

    assert backend.asked == [("owner-1", "c-a")]


# --- 只让它记得 -------------------------------------------------------------


def _audience(entry_id: str) -> str:
    return f"/api/management/v1/memory/entries/{entry_id}/audience"


async def test_a_memory_can_be_kept_between_me_and_one_eidolon(
    tmp_path, monkeypatch
) -> None:
    """The write side of the axis every read here already honours.

    One step, not two: I am looking at the memory when I name it, so there is no
    wording to resolve and nothing to bind into a token.
    """
    _stub_controller(monkeypatch, owner_id="owner-1")
    backend = _Backend()
    transport = httpx.ASGITransport(app=_app(tmp_path, backend))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        anonymous = await client.put(_audience("drawer_1"), json={"companion_id": "c-a"})
        headers = await _authenticate(client)
        answered = await client.put(
            _audience("drawer_1"), headers=headers, json={"companion_id": "c-a"}
        )

    assert anonymous.status_code == 401
    assert backend.asked == [("owner-1", "drawer_1", "c-a")]
    body = answered.json()
    assert body["entry_id"] == "drawer_1"
    assert body["companion_id"] == "c-a"
    assert body["status"] == "applied"


async def test_naming_no_eidolon_gives_the_memory_back_to_all_of_them(
    tmp_path, monkeypatch
) -> None:
    """The way back is the same call. A marking that could not be undone would be
    a delete wearing a friendlier word."""

    _stub_controller(monkeypatch, owner_id="owner-1")
    backend = _Backend()
    transport = httpx.ASGITransport(app=_app(tmp_path, backend))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        body = (
            await client.put(_audience("drawer_1"), headers=headers, json={})
        ).json()

    assert backend.asked == [("owner-1", "drawer_1", "")]
    assert body["companion_id"] == ""


async def test_an_accepted_marking_is_not_reported_as_done(tmp_path, monkeypatch) -> None:
    """Publishing is durable; applying is a projection still catching up. The
    client may only say 已经 when it reads ``applied``."""

    _stub_controller(monkeypatch, owner_id="owner-1")
    transport = httpx.ASGITransport(app=_app(tmp_path, _Backend()))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        body = (
            await client.put(_audience("drawer_1"), headers=headers, json={})
        ).json()

    assert body["status"] == "accepted"


async def test_the_marking_names_no_owner(tmp_path, monkeypatch) -> None:
    """The subject comes from the session. Here it is refused rather than
    ignored: this is a body, and the body forbids what it does not name."""

    _stub_controller(monkeypatch, owner_id="owner-1")
    backend = _Backend()
    transport = httpx.ASGITransport(app=_app(tmp_path, backend))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        answered = await client.put(
            _audience("drawer_1"),
            headers=headers,
            json={"companion_id": "c-a", "owner_id": "owner-2"},
        )

    assert answered.status_code == 422
    assert backend.asked == []


async def test_a_memory_that_is_not_there_is_relayed_as_not_found(
    tmp_path, monkeypatch
) -> None:
    _stub_controller(monkeypatch, owner_id="owner-1")
    transport = httpx.ASGITransport(app=_app(tmp_path, _Backend()))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        answered = await client.put(
            _audience("drawer_gone"), headers=headers, json={"companion_id": "c-a"}
        )

    assert answered.status_code == 404


# --- 你还记得…吗 ------------------------------------------------------------

_RECOLLECTIONS = "/api/management/v1/memory/recollections"


async def test_asking_what_it_remembers_answers_in_sentences(
    tmp_path, monkeypatch
) -> None:
    """Migrated from ``/api/local/v1/recollections``, which is now deleted.

    What travels is a sentence and a time. The wings, rooms and scores memory
    carries are how it found something rather than what it remembers, and a
    person asked the second question.
    """

    _stub_controller(monkeypatch, owner_id="owner-1")
    backend = _Backend()
    transport = httpx.ASGITransport(app=_app(tmp_path, backend))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        anonymous = await client.get(_RECOLLECTIONS, params={"q": "散步"})
        headers = await _authenticate(client)
        answered = await client.get(
            _RECOLLECTIONS, params={"q": "散步"}, headers=headers
        )

    assert anonymous.status_code == 401
    assert backend.asked == [("owner-1", "散步", 10, None)]
    body = answered.json()
    assert body["query"] == "散步"
    assert body["recollections"][0]["remembered_at"] == "2026-08-16T09:30:00Z"
    # Nothing about how it was found.
    assert "wing" not in answered.text
    assert "score" not in answered.text


async def test_a_question_is_required_and_an_unbounded_one_is_refused(
    tmp_path, monkeypatch
) -> None:
    """Refused here rather than passed down to memory."""

    _stub_controller(monkeypatch, owner_id="owner-1")
    backend = _Backend()
    transport = httpx.ASGITransport(app=_app(tmp_path, backend))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        empty = await client.get(_RECOLLECTIONS, headers=headers)
        huge = await client.get(
            _RECOLLECTIONS, params={"q": "x", "limit": 500}, headers=headers
        )

    assert empty.status_code == 422
    assert huge.status_code == 422
    assert backend.asked == []


async def test_a_recollection_may_be_asked_of_one_companions_audience(
    tmp_path, monkeypatch
) -> None:
    """As with the library: an audience, never a scope."""

    _stub_controller(monkeypatch, owner_id="owner-1")
    backend = _Backend()
    transport = httpx.ASGITransport(app=_app(tmp_path, backend))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        await client.get(
            _RECOLLECTIONS,
            params={"q": "茶", "limit": 3, "companion_id": "c-a"},
            headers=headers,
        )

    assert backend.asked == [("owner-1", "茶", 3, "c-a")]


# --- 它曾经是什么样 ---------------------------------------------------------


def _persona(companion_id: str = "companion-a") -> str:
    return f"/api/management/v1/companions/{companion_id}/persona-history"


def _restorations(companion_id: str = "companion-a") -> str:
    return f"/api/management/v1/companions/{companion_id}/persona-restorations"


async def test_a_person_is_shown_what_their_eidolon_became(tmp_path, monkeypatch) -> None:
    """Migrated from ``/api/local/v1/companions/{id}/persona``, now deleted.

    A record rather than a settings screen, and nothing about how a Companion is
    built: no genome hash, no schema version, no realizer.
    """

    _stub_controller(monkeypatch, owner_id="owner-1")
    backend = _Backend()
    transport = httpx.ASGITransport(app=_app(tmp_path, backend))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        anonymous = await client.get(_persona())
        headers = await _authenticate(client)
        answered = await client.get(_persona(), headers=headers)

    assert anonymous.status_code == 401
    assert backend.asked == [("owner-1", "companion-a")]
    chapters = answered.json()["chapters"]
    assert [chapter["chapter_id"] for chapter in chapters] == ["g_2", "g_1"]
    assert set(chapters[0]) == {
        "chapter_id",
        "changed_at",
        "what_changed",
        "restored_from",
        "is_current",
    }
    # Nothing was recorded for the first chapter, and nothing is invented.
    assert chapters[1]["what_changed"] == ""


async def test_going_back_answers_with_where_that_leaves_them(
    tmp_path, monkeypatch
) -> None:
    """The whole history, not the one new chapter.

    What someone wants to see after going back is where that leaves them, and a
    screen that had to ask again would show the old answer in the meantime.
    """

    _stub_controller(monkeypatch, owner_id="owner-1")
    backend = _Backend()
    transport = httpx.ASGITransport(app=_app(tmp_path, backend))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        restored = await client.put(
            _restorations(), headers=headers, json={"chapter_id": "g_1"}
        )

    assert restored.status_code == 200
    assert backend.asked == [("owner-1", "companion-a", "g_1")]
    chapters = {c["chapter_id"]: c["is_current"] for c in restored.json()["chapters"]}
    assert chapters == {"g_1": True, "g_2": False}


async def test_going_back_to_where_it_already_is_is_not_a_conflict(
    tmp_path, monkeypatch
) -> None:
    """A retry of a request whose answer was never seen asks for a state that
    already holds. The authority would refuse to append a chapter for it — and it
    is right to — but that refusal is not the person's answer."""

    _stub_controller(monkeypatch, owner_id="owner-1")
    backend = _Backend()
    transport = httpx.ASGITransport(app=_app(tmp_path, backend))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        first = await client.put(
            _restorations(), headers=headers, json={"chapter_id": "g_1"}
        )
        second = await client.put(
            _restorations(), headers=headers, json={"chapter_id": "g_1"}
        )

    assert (first.status_code, second.status_code) == (200, 200)
    assert first.json() == second.json()


async def test_a_chapter_it_never_was_cannot_be_returned_to(
    tmp_path, monkeypatch
) -> None:
    """A proposal is not a past. The authority refuses and the refusal is
    relayed rather than turned into a success."""

    _stub_controller(monkeypatch, owner_id="owner-1")
    transport = httpx.ASGITransport(app=_app(tmp_path, _Backend()))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        answered = await client.put(
            _restorations(), headers=headers, json={"chapter_id": "g_never"}
        )

    assert answered.status_code == 409


async def test_another_owners_persona_is_not_readable_or_restorable(
    tmp_path, monkeypatch
) -> None:
    """404 rather than 403, so an identifier cannot be probed for existence.

    The check is the authority's: these routes are keyed on a Companion alone, so
    ownership is proved through the owner-scoped Companion route before either of
    them runs.
    """

    _stub_controller(monkeypatch, owner_id="owner-1")
    transport = httpx.ASGITransport(app=_app(tmp_path, _Backend()))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        read = await client.get(_persona("companion-elsewhere"), headers=headers)
        wrote = await client.put(
            _restorations("companion-elsewhere"),
            headers=headers,
            json={"chapter_id": "g_1"},
        )

    assert read.status_code == 404
    assert wrote.status_code == 404
