"""Reading an Owner's memory library, and the paths it must not invent.

Discovery publishes one URL per memory space. The rest of ``/api/memory/v1/*``
sits beside it on that port, so a second read has to reach a sibling route —
and the tempting way to write that is to substitute one path for another in the
published string. That guesses at the realm's route layout and would happily
rewrite part of a host or a query, so it is composed from the prefix instead and
refused when the published URL is not in the family at all.

The other thing worth pinning: this client does no filtering. The realm applies
the same visibility policy recall uses; a second filter here would be a second
answer to "what may this person see", and the two would drift.
"""

from __future__ import annotations

import json

import httpx
import pytest

from eidolon_admin_server.app.control_plane.clients import MemoryRecollectionsClient
from eidolon_admin_server.app.control_plane.errors import AuthorityFailure

pytestmark = pytest.mark.asyncio

DISCOVERY = "http://memory-discovery.test"
OWNER = "owner-1"
TOKEN = "memory-api-token"

BROWSE = {
    "contract_version": "1",
    "operation": "memory.browse",
    "memory_space_id": "r_a",
    "wings": [
        {
            "wing_id": "Wing_Life",
            "is_configured": True,
            "display_name": "生活",
            "description": "",
            "sort_order": 8,
            "room_count": 1,
            "drawer_count": 2,
            "rooms": [
                {
                    "room_id": "饮食",
                    "drawer_count": 2,
                    "drawers_preview": [{"key": "d1", "preview": "乌龙茶"}],
                    "preview_truncated": True,
                }
            ],
        }
    ],
    "entry_count": 2,
    "withheld_count": 1,
    "truncated": False,
}


def _realm(realm_id: str = "r_a", *, url: str | None = None) -> dict:
    return {
        "memory_realm_id": realm_id,
        "owner_id": OWNER,
        "recollections_url": (
            url
            if url is not None
            else f"http://127.0.0.1:10031/api/memory/v1/recollections"
        ),
        "enabled": True,
        "agent_reachable": True,
    }


def _client(
    realms: list[dict],
    *,
    body: dict | None = None,
    service_token: str = TOKEN,
    seen: list[httpx.Request] | None = None,
):
    async def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if request.url.path == "/api/discovery/agent-routing":
            return httpx.Response(200, json={"memory_realms": realms})
        return httpx.Response(200, json=body if body is not None else BROWSE)

    return MemoryRecollectionsClient(
        discovery_url=DISCOVERY,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        timeout_seconds=1.0,
        service_token=service_token,
    )


async def test_browse_reaches_the_sibling_route_on_the_spaces_own_port() -> None:
    seen: list[httpx.Request] = []
    client = _client([_realm()], seen=seen)

    page = await client.browse(owner_id=OWNER)

    asked = next(r for r in seen if r.url.path != "/api/discovery/agent-routing")
    assert asked.url.path == "/api/memory/v1/browse"
    assert asked.url.port == 10031
    assert asked.headers["authorization"] == f"Bearer {TOKEN}"
    assert page.entry_count == 2


async def test_the_withheld_count_is_carried_rather_than_dropped() -> None:
    """A count that disagrees with what is listed explains itself; one that is
    silently absent leaves a person wondering why the numbers differ."""
    client = _client([_realm()])

    page = await client.browse(owner_id=OWNER)

    assert page.withheld_count == 1
    assert page.wings[0].rooms[0].preview_truncated is True


async def test_the_asking_companion_is_forwarded_as_an_audience() -> None:
    seen: list[httpx.Request] = []
    client = _client([_realm()], seen=seen)

    await client.browse(owner_id=OWNER, companion_id="c_mochi")

    asked = next(r for r in seen if r.url.path == "/api/memory/v1/browse")
    assert asked.url.params["companion_id"] == "c_mochi"


async def test_no_companion_sends_no_parameter() -> None:
    """Not an empty one: ``companion_id=`` would name a Companion with no id."""
    seen: list[httpx.Request] = []
    client = _client([_realm()], seen=seen)

    await client.browse(owner_id=OWNER)

    asked = next(r for r in seen if r.url.path == "/api/memory/v1/browse")
    assert "companion_id" not in asked.url.params


async def test_a_published_url_outside_the_family_is_a_contract_violation() -> None:
    """Rather than composing a path onto something this Admin cannot parse."""
    client = _client([_realm(url="http://127.0.0.1:10031/legacy/search")])

    with pytest.raises(AuthorityFailure) as caught:
        await client.browse(owner_id=OWNER)

    assert caught.value.kind == "contract_violation"


async def test_a_host_without_the_credential_says_so_before_dialling() -> None:
    client = _client([_realm()], service_token="")

    with pytest.raises(AuthorityFailure) as caught:
        await client.browse(owner_id=OWNER)

    assert caught.value.kind == "configuration"
    assert caught.value.retryable is False


async def test_two_spaces_for_one_owner_refuse_to_be_browsed() -> None:
    """Same rule as the search read: one Owner is one Realm.

    Browsing whichever sorted first would show a person one Companion's memory
    and call it theirs.
    """
    client = _client([_realm("r_a"), _realm("r_b")])

    with pytest.raises(AuthorityFailure) as caught:
        await client.browse(owner_id=OWNER)

    assert caught.value.kind == "conflict"


async def test_an_answer_outside_the_contract_is_not_relayed() -> None:
    """A shape this Admin cannot parse is a violation, not an empty library."""
    client = _client([_realm()], body={"operation": "memory.browse"})

    with pytest.raises(AuthorityFailure) as caught:
        await client.browse(owner_id=OWNER)

    assert caught.value.kind == "contract_violation"


async def test_a_wing_this_admin_has_never_heard_of_still_parses() -> None:
    """The producer may grow the wing schema; a strict consumer must not break.

    ``wing_id`` is a plain string for the same reason ``kind`` is on a Companion
    — the set is the producer's to extend, and an otherwise readable answer must
    stay readable.
    """
    body = {
        **BROWSE,
        "wings": [
            {**BROWSE["wings"][0], "wing_id": "Wing_FromALaterRelease", "is_configured": False}
        ],
    }
    client = _client([_realm()], body=body)

    page = await client.browse(owner_id=OWNER)

    assert page.wings[0].wing_id == "Wing_FromALaterRelease"
    assert page.wings[0].is_configured is False


PREVIEW = {
    "contract_version": "1",
    "operation": "memory.forget-preview",
    "status": "preview",
    "target": "上周那件事",
    "action": "delete",
    "entries": [{"drawer_id": "drawer_1", "score": 0.8, "preview": "上周那件事"}],
    "needs_confirmation": True,
    "confirmation_token": "opaque",
    "expires_at": 1900000000,
    "detail": "",
}

CONFIRMED = {
    "contract_version": "1",
    "operation": "memory.forget-confirm",
    "action": "delete",
    "target": "上周那件事",
    "entry_count": 1,
    "status": "accepted",
    "request_id": "r1",
}


async def test_a_preview_is_a_post_to_the_realms_own_route() -> None:
    """A read that mints a token is not a GET.

    It has a side effect the caller depends on — the binding — and it must not
    be cached or replayed by anything between here and the realm.
    """
    seen: list[httpx.Request] = []
    client = _client([_realm()], body=PREVIEW, seen=seen)

    proposal = await client.forget_preview(owner_id=OWNER, target="上周那件事")

    asked = next(r for r in seen if r.url.path == "/api/memory/v1/forget/preview")
    assert asked.method == "POST"
    assert asked.url.params["target"] == "上周那件事"
    assert asked.url.params["action"] == "delete"
    assert asked.headers["authorization"] == f"Bearer {TOKEN}"
    assert proposal.entries[0].drawer_id == "drawer_1"


async def test_the_confirm_sends_the_token_and_nothing_else() -> None:
    """Not the target. Sending both would invite the realm to prefer the wrong one."""
    seen: list[httpx.Request] = []
    client = _client([_realm()], body=CONFIRMED, seen=seen)

    result = await client.forget_confirm(owner_id=OWNER, confirmation_token="opaque")

    asked = next(r for r in seen if r.url.path == "/api/memory/v1/forget/confirm")
    assert dict(asked.url.params) == {"confirmation_token": "opaque"}
    assert result.entry_count == 1
    assert result.status == "accepted"


async def test_the_ledgers_own_fields_survive_the_parse() -> None:
    """The command ledger's vocabulary is not this contract's to pin.

    ``request_id`` here comes from the ledger. Forbidding unknown fields on this
    one model would make every ledger addition an Admin release.
    """
    client = _client([_realm()], body=CONFIRMED)

    result = await client.forget_confirm(owner_id=OWNER, confirmation_token="opaque")

    assert result.model_extra["request_id"] == "r1"


async def test_a_host_without_the_credential_refuses_before_dialling() -> None:
    client = _client([_realm()], body=PREVIEW, service_token="")

    with pytest.raises(AuthorityFailure) as caught:
        await client.forget_preview(owner_id=OWNER, target="x")

    assert caught.value.kind == "configuration"


DAY = {
    "contract_version": "1",
    "operation": "memory.entries",
    "memory_space_id": "r_a",
    "since": "2026-08-24T12:00:00+00:00",
    "entries": [
        {
            "entry_id": "drawer_1",
            "recorded_at": "2026-08-24T12:30:00+00:00",
            "recorded_at_source": "occurred_at",
            "wing_id": "Wing_Life",
            "room_id": "饮食",
            "preview": "乌龙茶",
        }
    ],
    "entry_count": 1,
    "more_in_window": False,
    "undated_count": 1,
    "truncated": False,
}


async def test_entries_reach_the_sibling_route_with_the_window_intact() -> None:
    """``since`` is relayed exactly as given.

    Rewriting or defaulting it here would be this client answering for a
    timezone it does not know, and being wrong by up to a day without saying so.
    """
    seen: list[httpx.Request] = []
    client = _client([_realm()], body=DAY, seen=seen)

    day = await client.entries(owner_id=OWNER, since="2026-08-24T12:00:00+00:00")

    asked = next(r for r in seen if r.url.path == "/api/memory/v1/entries")
    assert asked.url.params["since"] == "2026-08-24T12:00:00+00:00"
    assert asked.headers["authorization"] == f"Bearer {TOKEN}"
    assert day.entries[0].entry_id == "drawer_1"
    assert day.undated_count == 1


async def test_an_absent_limit_and_audience_send_nothing() -> None:
    """Not empty strings: ``limit=`` is not a number and ``companion_id=`` names
    an Eidolon with no id."""
    seen: list[httpx.Request] = []
    client = _client([_realm()], body=DAY, seen=seen)

    await client.entries(owner_id=OWNER, since="2026-08-24T12:00:00+00:00")

    asked = next(r for r in seen if r.url.path == "/api/memory/v1/entries")
    assert set(asked.url.params) == {"since"}


async def test_a_day_read_shares_the_credential_check_with_every_other_realm_read() -> None:
    """All three reads go through one helper, so one of them cannot drift open."""
    client = _client([_realm()], body=DAY, service_token="")

    with pytest.raises(AuthorityFailure) as caught:
        await client.entries(owner_id=OWNER, since="2026-08-24T12:00:00+00:00")

    assert caught.value.kind == "configuration"


COPY = {
    "contract_version": "1",
    "operation": "memory.export",
    "memory_space_id": "r_a",
    "taken_at": "2026-08-24T12:31:00+00:00",
    "records": [
        {
            "entry_id": "drawer_1",
            "recorded_at": "2026-08-24T12:30:00+00:00",
            "recorded_at_source": "occurred_at",
            "wing_id": "Wing_Life",
            "room_id": "饮食",
            "memory_type": "preference",
            "value": "他喜欢喝乌龙茶，" * 30,
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


async def test_the_copy_reaches_the_sibling_route_and_arrives_whole() -> None:
    """The one read on this surface that must not shorten anything.

    A relay, not an assembler: a file built here would be built out of whatever
    this process asked for, and only the realm knows what "everything I can see"
    is.
    """
    seen: list[httpx.Request] = []
    client = _client([_realm()], body=COPY, seen=seen)

    copy = await client.export(owner_id=OWNER)

    asked = next(r for r in seen if r.url.path == "/api/memory/v1/export")
    assert asked.method == "GET"
    assert asked.headers["authorization"] == f"Bearer {TOKEN}"
    assert copy.records[0].value == COPY["records"][0]["value"]
    # Both honesty counts survive the parse: one says why part of the file has
    # no dates, the other says the file is part of a memory.
    assert copy.undated_count == 1
    assert copy.truncated is True


async def test_a_copy_of_no_audience_in_particular_sends_no_parameter() -> None:
    seen: list[httpx.Request] = []
    client = _client([_realm()], body=COPY, seen=seen)

    await client.export(owner_id=OWNER)

    asked = next(r for r in seen if r.url.path == "/api/memory/v1/export")
    assert set(asked.url.params) == set()


async def test_a_copy_shares_the_credential_check_with_every_other_realm_read() -> None:
    """Four reads, one helper: none of them can drift open on its own."""

    client = _client([_realm()], body=COPY, service_token="")

    with pytest.raises(AuthorityFailure) as caught:
        await client.export(owner_id=OWNER)

    assert caught.value.kind == "configuration"


AUDIENCE = {
    "contract_version": "1",
    "operation": "memory.audience",
    "entry_id": "drawer_1",
    "audience": "companion:c_mochi",
    "companion_id": "c_mochi",
    "status": "accepted",
}


async def test_marking_a_memory_puts_the_entry_in_the_path_and_the_eidolon_in_the_body() -> None:
    """A PUT on the entry: the body is the desired end state of one exact record,
    so a client that never saw the answer can send it again."""

    seen: list[httpx.Request] = []
    client = _client([_realm()], body=AUDIENCE, seen=seen)

    result = await client.assign_audience(
        owner_id=OWNER, entry_id="drawer_1", companion_id="c_mochi"
    )

    asked = next(r for r in seen if "/audience" in r.url.path)
    assert asked.method == "PUT"
    assert asked.url.path == "/api/memory/v1/entries/drawer_1/audience"
    assert json.loads(asked.content) == {"companion_id": "c_mochi"}
    assert result.companion_id == "c_mochi"
    # The ledger's word, relayed rather than turned into success.
    assert result.status == "accepted"


async def test_giving_a_memory_back_names_no_companion() -> None:
    """An empty string rather than the word "owner": the realm decides what an
    audience is, and absence is how this layer says "all of them"."""

    seen: list[httpx.Request] = []
    client = _client(
        [_realm()],
        body={**AUDIENCE, "audience": "owner", "companion_id": ""},
        seen=seen,
    )

    await client.assign_audience(owner_id=OWNER, entry_id="drawer_1")

    asked = next(r for r in seen if "/audience" in r.url.path)
    assert json.loads(asked.content) == {"companion_id": ""}


async def test_an_entry_id_is_quoted_into_the_path() -> None:
    """It comes from a page, not from this process, so it is escaped rather than
    trusted to be path-safe."""

    seen: list[httpx.Request] = []
    client = _client([_realm()], body=AUDIENCE, seen=seen)

    await client.assign_audience(
        owner_id=OWNER, entry_id="drawer_1/../other", companion_id="c_mochi"
    )

    asked = next(r for r in seen if "/audience" in r.url.path)
    # ``raw_path``, because ``path`` hands back the decoded form — and the escape
    # is exactly what is being asserted.
    assert asked.url.raw_path.endswith(
        b"/api/memory/v1/entries/drawer_1%2F..%2Fother/audience"
    )


async def test_a_marking_shares_the_credential_check_with_every_other_realm_call() -> None:
    client = _client([_realm()], body=AUDIENCE, service_token="")

    with pytest.raises(AuthorityFailure) as caught:
        await client.assign_audience(
            owner_id=OWNER, entry_id="drawer_1", companion_id="c_mochi"
        )

    assert caught.value.kind == "configuration"
