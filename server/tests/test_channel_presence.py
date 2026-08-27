"""Whether a body is on its channel — the only presence this Host can observe.

Presence had no producer at all. Hub publishes existence and lifecycle and a
contract test forbids liveness on its device directory; Kernel commits an
assignment and speaks about the assignment, not the body; the runtime
blackboard's reader was withdrawn with the data-v2 refactor. So every body on
the Owner's map read 「未探测」 while its speaker was plainly in a call — true,
and useless.

The channel is what carries the body, so it is what knows. These tests hold the
three things that make the answer worth trusting:

* **on a channel is not the same as powered on**, and neither is a claim about
  the other. A body the channel could not see is ``unknown``;
* **precedence is about standing, not about who answered.** The channel sits
  below the blackboard and above Hub whether or not those two say anything;
* **a mismatch is loud.** If the channel's bodies and the Owner's bodies turn
  out not to be named the same way, the lane degrades with that sentence in it
  rather than reading as an Owner who owns nothing.
"""

from __future__ import annotations

from eidolon_admin_server.app.mission_control.lanes import LaneLedger
from eidolon_admin_server.app.mission_control.schemas import RuntimeDeviceBlackboard
from eidolon_admin_server.app.mission_control.service import (
    _device_presence,
    _merge_devices,
)

_BOX = "device-instance-box3"


def _blackboard() -> RuntimeDeviceBlackboard:
    """The presence authority that no longer answers on this Host."""

    return RuntimeDeviceBlackboard(health="unexposed", detail="withdrawn", key="k")


def _merged(presence: dict[str, bool | None]):
    return _merge_devices(
        [],
        [],
        runtime_blackboard=_blackboard(),
        owner_id="owner-1",
        channel_presence=presence,
    )


def test_a_body_on_its_channel_is_online_and_says_who_said_so() -> None:
    rows = _merged({_BOX: True})

    assert [(row.device_id, row.status, row.online) for row in rows] == [
        (_BOX, "online", True)
    ]
    assert rows[0].signals["presence_source"] == "channel"


def test_a_body_the_channel_cannot_see_is_unknown_not_offline() -> None:
    """The distinction the third state exists for.

    `offline` tells someone their speaker is off. `unknown` says nobody with
    standing looked. Collapsing them is the one thing this lane must not do.
    """

    rows = _merged({_BOX: None})

    assert (rows[0].status, rows[0].online) == ("unknown", False)
    assert rows[0].signals["presence_source"] == "channel"


def test_a_body_with_a_channel_it_is_not_on_is_offline() -> None:
    rows = _merged({_BOX: False})

    assert (rows[0].status, rows[0].online) == ("offline", False)


def test_presence_seeds_bodies_no_other_source_here_mentions() -> None:
    """On this Host no other source does.

    The inventory is Claims joined with mounts and both want a credential this
    process does not hold — which is exactly why presence had nothing to be
    joined onto. A body the channel is carrying is a body that exists.
    """

    assert [row.device_id for row in _merged({_BOX: True})] == [_BOX]


def test_the_blackboard_still_outranks_the_channel() -> None:
    """Precedence is standing, not availability."""

    class _Entry:
        def is_online(self) -> bool:
            return False

    status, online, source = _device_presence(_Entry(), None, on_channel=True)

    assert (status, online, source) == ("offline", False, "runtime_blackboard")


def test_a_body_nobody_was_asked_about_falls_through_to_hub() -> None:
    """Not asked is not the same as asked and unanswerable."""

    class _Hub:
        status = "online"

    status, online, source = _device_presence(None, _Hub())

    assert (status, online, source) == ("online", True, "hub")


# ── the read itself ────────────────────────────────────────────────────────


class _Answer:
    def __init__(self, payload) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._payload


class _Http:
    def __init__(self, payload=None, error: Exception | None = None) -> None:
        self._payload = payload
        self._error = error
        self.urls: list[str] = []

    async def get(self, url, params=None, timeout=None, headers=None):
        self.urls.append(url)
        if self._error is not None:
            raise self._error
        return _Answer(self._payload)


class _Service:
    base_url = "http://127.0.0.1:8767"
    upstream_prefix = ""

    class auth:  # noqa: N801 - mirrors the settings model's shape
        type = "none"
        token_env = None


class _Registry:
    def __init__(self, present: bool = True) -> None:
        self._present = present

    def get(self, service_id: str):
        return _Service() if self._present and service_id == "channel-provider" else None


class _State:
    def __init__(self, http, registry) -> None:
        self.http_client = http
        self.registry = registry


class _App:
    def __init__(self, http, registry) -> None:
        self.state = _State(http, registry)


class _Request:
    def __init__(self, http, registry=None) -> None:
        self.app = _App(http, registry or _Registry())


async def _read(http, registry=None):
    from eidolon_admin_server.app.mission_control.service import _channel_presence

    ledger = LaneLedger()
    presence = await _channel_presence(_Request(http, registry), "owner-1", ledger)
    return presence, ledger


async def test_only_this_owners_bodies_are_kept() -> None:
    presence, ledger = await _read(
        _Http(
            {
                "bodies": [
                    {"device_id": _BOX, "owner_id": "owner-1", "on_channel": True},
                    {"device_id": "someone-elses", "owner_id": "owner-2", "on_channel": True},
                ]
            }
        )
    )

    assert presence == {_BOX: True}
    assert any(status.source == "channel.presence" and status.ok for status in ledger.statuses())


async def test_a_read_that_matched_nobody_degrades_the_lane_with_the_reason() -> None:
    """The silent failure this guards against would have hidden for months.

    If the channel's device ids and the Owner's are not the same names, dropping
    every row reads exactly like an Owner with no bodies. So it is reported.
    """

    presence, ledger = await _read(
        _Http({"bodies": [{"device_id": "x", "owner_id": "other", "on_channel": True}]})
    )

    assert presence == {}
    failure = next(s for s in ledger.statuses() if s.source == "channel.presence")
    assert not failure.ok
    assert "named the same way" in failure.detail


async def test_an_unreachable_provider_is_a_failed_read_not_an_empty_one() -> None:
    presence, ledger = await _read(_Http(error=RuntimeError("connection refused")))

    assert presence == {}
    failure = next(s for s in ledger.statuses() if s.source == "channel.presence")
    assert not failure.ok
    assert "connection refused" in failure.detail


async def test_an_unregistered_provider_is_reported_rather_than_ignored() -> None:
    """A Host installed before this entry existed. It must not read as healthy."""

    presence, ledger = await _read(_Http({"bodies": []}), _Registry(present=False))

    assert presence == {}
    assert not next(s for s in ledger.statuses() if s.source == "channel.presence").ok
