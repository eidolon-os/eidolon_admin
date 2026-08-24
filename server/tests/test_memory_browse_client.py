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
