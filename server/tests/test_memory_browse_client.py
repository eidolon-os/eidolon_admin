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

Every realm answer below is built from the shared Owner contract
(``eidolon_memory_contracts.owner``) — the models the realm serializes through —
rather than written out by hand. Hand-written replies in this file once agreed
with this Admin and with nothing the realm ever sent, and that is how "forget"
failed on every real match with every suite green.
"""

from __future__ import annotations

import logging

import httpx
import pytest
from eidolon_admin_server.app.control_plane.clients import MemoryRecollectionsClient
from eidolon_admin_server.app.control_plane.errors import AuthorityFailure
from eidolon_memory_contracts.owner import (
    MemoryBrowse,
    MemoryEntries,
    MemoryExport,
    MemoryRecollections,
    MemoryStatus,
    OwnerForgetOutcome,
    OwnerForgetPreview,
    OwnerForgetProgress,
)

pytestmark = pytest.mark.asyncio

DISCOVERY = "http://memory-discovery.test"
OWNER = "owner-1"
TOKEN = "memory-api-token"

BROWSE = MemoryBrowse.model_validate({
    "memory_space_id": "r_a",
    "audience_scope": "owner",
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
}).model_dump(mode="json")

GRAPH = {
    "contract_version": "1",
    "operation": "memory.graph",
    "memory_space_id": "r_a",
    "nodes": [
        {"node_id": "self", "label": "我", "degree": 1},
        {"node_id": "tea", "label": "乌龙茶", "degree": 1},
    ],
    "edges": [
        {
            "edge_id": "stmt_1",
            "subject": "我",
            "predicate": "likes",
            "object": "乌龙茶",
            "confidence": 0.94,
            "recorded_at": "2026-08-28T08:00:00Z",
        }
    ],
    "truncated": False,
}


def _realm(realm_id: str = "r_a", *, url: str | None = None) -> dict:
    return {
        "memory_realm_id": realm_id,
        "owner_id": OWNER,
        "recollections_url": (
            url
            if url is not None
            else "http://127.0.0.1:10031/api/memory/v1/recollections"
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


async def test_graph_uses_the_same_realm_and_companion_audience() -> None:
    seen: list[httpx.Request] = []
    client = _client([_realm()], body=GRAPH, seen=seen)

    graph = await client.graph(owner_id=OWNER, companion_id="c_mochi")

    asked = next(r for r in seen if r.url.path == "/api/memory/v1/graph")
    assert asked.url.params["companion_id"] == "c_mochi"
    assert graph.edges[0].predicate == "likes"
    assert graph.nodes[1].label == "乌龙茶"


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


PREVIEW = OwnerForgetPreview.model_validate({
    "status": "preview",
    "target": "上周那件事",
    "entries": [{"entry_id": "drawer_1", "score": 0.8, "preview": "上周那件事的记录"}],
    "needs_confirmation": True,
    "confirmation_token": "opaque",
    "expires_at": 1900000000,
}).model_dump(mode="json")

CONFIRMED = OwnerForgetOutcome(
    request_id="owner-forget-p1",
    target="上周那件事",
    entry_count=1,
    status="accepted",
).model_dump(mode="json")

PROGRESS = OwnerForgetProgress(request_id="owner-forget-p1", status="applied").model_dump(
    mode="json"
)


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
    # Only the words: an Owner forget is a delete, and there is no action to name.
    assert dict(asked.url.params) == {"target": "上周那件事"}
    assert asked.headers["authorization"] == f"Bearer {TOKEN}"
    assert proposal.entries[0].entry_id == "drawer_1"
    assert proposal.entries[0].preview == "上周那件事的记录"


async def test_the_confirm_sends_the_token_and_nothing_else() -> None:
    """Not the target. Sending both would invite the realm to prefer the wrong one."""
    seen: list[httpx.Request] = []
    client = _client([_realm()], body=CONFIRMED, seen=seen)

    result = await client.forget_confirm(owner_id=OWNER, confirmation_token="opaque")

    asked = next(r for r in seen if r.url.path == "/api/memory/v1/forget/confirm")
    assert dict(asked.url.params) == {"confirmation_token": "opaque"}
    assert result.entry_count == 1
    assert result.status == "accepted"


async def test_the_confirm_names_the_change_it_made() -> None:
    """``request_id`` is part of the contract now, not an extra that survives it.

    It is what a client asks about afterwards; carried only as an untyped extra,
    it was dropped one layer up and the phone had nothing to ask with.
    """
    client = _client([_realm()], body=CONFIRMED)

    result = await client.forget_confirm(owner_id=OWNER, confirmation_token="opaque")

    assert result.request_id == "owner-forget-p1"


async def test_where_a_forget_got_to_is_read_from_the_realm() -> None:
    seen: list[httpx.Request] = []
    client = _client([_realm()], body=PROGRESS, seen=seen)

    progress = await client.forget_status(owner_id=OWNER, request_id="owner-forget-p1")

    asked = next(r for r in seen if r.url.path == "/api/memory/v1/forget/status")
    assert asked.method == "GET"
    assert dict(asked.url.params) == {"request_id": "owner-forget-p1"}
    assert progress.status == "applied"


async def test_a_violation_names_the_fields_and_never_their_values(caplog) -> None:
    """The fixed sentence alone is why a month of failed previews had no cause.

    The log names which field broke and how. It never carries the value: this
    answer is what a person said.
    """
    drifted = {**PREVIEW, "entries": [{"id": "drawer_1", "text": "我的工资是两万", "score": 1}]}
    client = _client([_realm()], body=drifted)

    with caplog.at_level(logging.WARNING, logger="eidolon_admin.authority"):
        with pytest.raises(AuthorityFailure) as caught:
            await client.forget_preview(owner_id=OWNER, target="工资")

    assert caught.value.kind == "contract_violation"
    logged = caplog.text
    assert "OwnerForgetPreview" in logged
    assert "entries.0.entry_id:missing" in logged
    assert "entries.0.text:extra_forbidden" in logged
    assert "两万" not in logged


async def test_a_rejected_credential_on_a_search_is_not_an_outage() -> None:
    """Recollections map refusals by status like every other realm read.

    It used to answer every non-200 as a retryable "memory did not answer", so a
    rotated credential read as memory being down.
    """

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/discovery/agent-routing":
            return httpx.Response(200, json={"memory_realms": [_realm()]})
        return httpx.Response(401, json={"detail": "memory service credential rejected"})

    client = MemoryRecollectionsClient(
        discovery_url=DISCOVERY,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        timeout_seconds=1.0,
        service_token=TOKEN,
    )

    with pytest.raises(AuthorityFailure) as caught:
        await client.recollections(owner_id=OWNER, query="茶", limit=5)

    assert caught.value.kind == "unauthorized"
    assert caught.value.retryable is False


async def test_a_search_answer_is_the_shared_contract() -> None:
    body = MemoryRecollections.model_validate(
        {
            "memory_space_id": "r_a",
            "query": "茶",
            "recollections": [{"text": "喜欢乌龙茶", "remembered_at": "2026-08-28T04:24:36Z"}],
        }
    ).model_dump(mode="json")
    client = _client([_realm()], body=body)

    found = await client.recollections(owner_id=OWNER, query="茶", limit=5)

    assert [item.text for item in found.recollections] == ["喜欢乌龙茶"]


async def test_a_host_without_the_credential_refuses_before_dialling() -> None:
    client = _client([_realm()], body=PREVIEW, service_token="")

    with pytest.raises(AuthorityFailure) as caught:
        await client.forget_preview(owner_id=OWNER, target="x")

    assert caught.value.kind == "configuration"


DAY = MemoryEntries.model_validate({
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
}).model_dump(mode="json")


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


async def test_an_older_page_relays_the_realms_cursor_unread() -> None:
    seen: list[httpx.Request] = []
    body = {**DAY, "more_in_window": True, "next_cursor": "opaque-position"}
    client = _client([_realm()], body=body, seen=seen)

    first = await client.entries(owner_id=OWNER, since="2026-08-24T12:00:00+00:00")
    await client.entries(
        owner_id=OWNER, since="2026-08-24T12:00:00+00:00", cursor=first.next_cursor
    )

    asked = [r for r in seen if r.url.path == "/api/memory/v1/entries"][-1]
    assert asked.url.params["cursor"] == "opaque-position"


async def test_a_day_read_shares_the_credential_check_with_every_other_realm_read() -> None:
    """All three reads go through one helper, so one of them cannot drift open."""
    client = _client([_realm()], body=DAY, service_token="")

    with pytest.raises(AuthorityFailure) as caught:
        await client.entries(owner_id=OWNER, since="2026-08-24T12:00:00+00:00")

    assert caught.value.kind == "configuration"


COPY = MemoryExport.model_validate({
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
            "audience": "companion:c-a",
            "value": "他喜欢喝乌龙茶，" * 30,
        },
        {
            "entry_id": "drawer_undated",
            "recorded_at": "",
            "recorded_at_source": "",
            "wing_id": "",
            "room_id": "",
            "memory_type": "",
            "audience": "owner",
            # Longer than the old 65,536-character cap, which failed the whole
            # copy over one long memory.
            "value": "说不清什么时候" * 12000,
        },
    ],
    "record_count": 2,
    "undated_count": 1,
    "truncated": True,
}).model_dump(mode="json")


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
    assert copy.records[0].audience == "companion:c-a"
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


STATUS = MemoryStatus.model_validate({
    "memory_realm_id": "r_a",
    "memory_space_id": "r_a",
    "audience_scope": "companion:c_mochi",
    "ready": True,
    "data_readable": True,
    "materialization_state": "ready",
    "projection_pending": 0,
    "last_materialized_at": "2026-08-29T12:00:00Z",
    "degraded_reason": "",
}).model_dump(mode="json")


async def test_status_uses_the_realm_authority_and_forwards_scope() -> None:
    seen: list[httpx.Request] = []
    client = _client([_realm()], body=STATUS, seen=seen)

    result = await client.status(owner_id=OWNER, companion_id="c_mochi")

    asked = next(r for r in seen if r.url.path == "/api/memory/v1/status")
    assert asked.method == "GET"
    assert asked.url.params["companion_id"] == "c_mochi"
    assert result.data_readable is True
    assert result.materialization_state == "ready"
