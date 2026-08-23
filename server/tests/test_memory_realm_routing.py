"""How Admin picks a Memory Realm, and what it says when it cannot.

Owner-level memory is the ratified model (docs/跨系统/多Companion记忆隔离机制裁决.md):
one Owner, one Realm. That makes the Realm lookup a *uniqueness* check rather
than a search — "take the first match" would silently answer from whichever
Realm sorted first, which is the one failure mode this file exists to forbid.

Three outcomes have to stay distinguishable, because the next action a person
takes differs for each:

  - this Owner has no Realm            → not_found
  - this Owner has more than one       → conflict (migration incomplete or bad data)
  - the Realm exists but is not running → runtime_missing, NOT "memory is down"
"""

from __future__ import annotations

import httpx
import pytest

from eidolon_admin_server.app.control_plane.clients import MemoryRecollectionsClient
from eidolon_admin_server.app.control_plane.errors import AuthorityFailure

pytestmark = [pytest.mark.asyncio, pytest.mark.component]

DISCOVERY = "http://discovery.test"
OWNER = "o_1"


def _realm(
    realm_id: str,
    *,
    owner_id: str = OWNER,
    companion_id: str | None = None,
    enabled: bool = True,
    agent_reachable: bool = True,
    recollections_url: str | None = None,
) -> dict:
    return {
        "memory_space_id": realm_id,
        "memory_realm_id": realm_id,
        "owner_id": owner_id,
        "companion_id": companion_id,
        "enabled": enabled,
        "agent_reachable": agent_reachable,
        "recollections_url": (
            recollections_url
            if recollections_url is not None
            else f"http://realm.test/{realm_id}/api/memory/v1/recollections"
        ),
    }


def _client(realms: list[dict], *, recollections: dict | None = None):
    """A discovery answering ``realms``, and Realms answering ``recollections``."""

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/discovery/agent-routing":
            return httpx.Response(200, json={"memory_realms": realms})
        if recollections is None:
            raise httpx.ConnectError("realm runtime is not listening", request=request)
        return httpx.Response(200, json=recollections)

    transport = httpx.MockTransport(handler)
    return MemoryRecollectionsClient(
        discovery_url=DISCOVERY,
        client=httpx.AsyncClient(transport=transport),
        timeout_seconds=1.0,
    )


async def _failure(client, **kwargs) -> AuthorityFailure:
    with pytest.raises(AuthorityFailure) as caught:
        await client.recollections(owner_id=OWNER, query="q", limit=1, **kwargs)
    return caught.value


async def test_single_owner_realm_answers() -> None:
    client = _client(
        [_realm("r_a")],
        recollections={"recollections": [{"text": "remembered"}]},
    )
    found = await client.recollections(owner_id=OWNER, query="q", limit=1)
    assert found == [{"text": "remembered"}]


async def test_other_owners_realms_are_not_reachable() -> None:
    client = _client([_realm("r_b", owner_id="o_other")])
    failure = await _failure(client)
    assert failure.kind == "not_found"
    assert failure.status_code == 404


async def test_two_realms_for_one_owner_fail_closed() -> None:
    """The dangerous case: never answer from whichever sorted first.

    Two active Realms under one Owner means the Owner-level migration is
    incomplete or the data is wrong. Answering from either one would be a
    coin flip between two Companions' memories.
    """

    client = _client(
        [_realm("r_a"), _realm("r_b")],
        recollections={"recollections": []},
    )
    failure = await _failure(client)
    assert failure.kind == "conflict"
    assert failure.status_code == 409
    # Both are named, so whoever fixes the data knows which two.
    assert "r_a" in failure.detail and "r_b" in failure.detail


async def test_disabled_realm_is_runtime_missing_not_authority_down() -> None:
    client = _client([_realm("r_a", enabled=False)])
    failure = await _failure(client)
    assert failure.kind == "runtime_missing"
    assert failure.retryable is True


async def test_unreachable_realm_is_runtime_missing_before_it_is_dialled() -> None:
    """Discovery publishes a URL whether or not the runtime is listening.

    Discovery derives ``recollections_url`` from the port and reports liveness
    separately in ``agent_reachable``. Dialling a dead port and reporting the
    connect error would say "memory is unreachable" — the wrong problem, and
    the wrong next action.
    """

    client = _client([_realm("r_a", agent_reachable=False)])
    failure = await _failure(client)
    assert failure.kind == "runtime_missing"
    assert "r_a" in failure.detail


async def test_realm_without_read_surface_is_a_contract_violation() -> None:
    client = _client([_realm("r_a", recollections_url="")])
    failure = await _failure(client)
    assert failure.kind == "contract_violation"


async def test_every_memory_failure_survives_the_wire() -> None:
    """Each failure must be expressible in the shape the router serialises.

    ``WorkflowFailure`` pins both the authority and the kind. A value outside
    those sets does not degrade — it raises inside the exception handler, and
    the caller gets a 500 describing nothing instead of the status this code
    chose. Every failure this client can raise is checked here.
    """

    cases = [
        _client([]),
        _client([_realm("r_a"), _realm("r_b")]),
        _client([_realm("r_a", enabled=False)]),
        _client([_realm("r_a", agent_reachable=False)]),
        _client([_realm("r_a", recollections_url="")]),
        _client([_realm("r_a")]),  # realm dialled, connection refused
    ]
    for client in cases:
        failure = await _failure(client)
        wire = failure.to_wire()
        assert wire.authority == "memory"
        assert wire.kind == failure.kind
