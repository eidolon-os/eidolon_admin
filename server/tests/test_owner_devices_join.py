"""Who exists, joined with who is present — and never one read as the other.

The star map used to draw only the bodies the runtime blackboard knew about,
while the same Owner's device list drew every body they own. Two surfaces, one
Owner, different answers to "how many bodies do I have".

They disagreed because the two facts have different authorities and only one of
them is reachable from the process that composes Mission Control: existence is
Claims (Hub) joined with mounts (Kernel), read from a Controller session on this
plane; presence is the owner-isolated blackboard, read in the Admin process. So
the join happens here, and what these tests hold is that it stays a join:

* a body the blackboard has never seen is drawn, and drawn as **unknown** —
  never offline, because nobody with standing said;
* a body the blackboard did answer for keeps that answer; the inventory may lend
  it a name and a binding and nothing else;
* one side failing leaves the lane partly known rather than empty or whole.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from eidolon_admin_server.local_api.management.mission_control import join_owner_devices
from eidolon_admin_server.local_api.management.router import _device_view


def _snapshot(*, state: str = "ok", detail: str = "", items=None) -> dict:
    return {
        "contract_version": "1",
        "coverage": "owner-runtime",
        "devices": {
            "state": state,
            "detail": detail,
            "observed_at": "2026-08-26T12:30:00Z",
            "latency_ms": 4,
            "truncated": False,
            "items": items if items is not None else [],
        },
    }


def _present(device_id: str, **overrides) -> dict:
    row = {
        "device_id": device_id,
        "display_name": "",
        "device_kind": "",
        "role": "未绑定",
        "role_kind": "unbound",
        "companion_id": None,
        "capabilities": ["voice.duplex"],
        "presence": {
            "state": "online",
            "source": "runtime_blackboard",
            "observed_at": "2026-08-26T12:29:59Z",
        },
    }
    row.update(overrides)
    return row


def _inventory(*devices) -> SimpleNamespace:
    return SimpleNamespace(devices=list(devices))


def _owned(device_id: str, *, label: str = "esp-box-3", kind: str = "esp32-s3", companion=None):
    return SimpleNamespace(
        device_id=device_id,
        label=label,
        kind=kind,
        answers_as_companion_id=companion,
        # The device list refuses to infer presence and says so; this join must
        # not quietly promote it.
        online="unknown",
        online_reason="这台主机没有任何东西在观测设备是否开着",
    )


def test_a_body_the_blackboard_never_saw_is_drawn_as_unknown() -> None:
    joined = join_owner_devices(
        _snapshot(),
        devices=[_owned("dev-quiet", companion="companion-a")],
    )

    lane = joined["devices"]
    assert lane["state"] == "ok"
    assert [row["device_id"] for row in lane["items"]] == ["dev-quiet"]
    row = lane["items"][0]
    assert row["display_name"] == "esp-box-3"
    assert row["companion_id"] == "companion-a"
    assert row["role_kind"] == "persona"
    # An active Claim and a Kernel mount say this body is known. Neither says it
    # is switched on, and "offline" would tell someone their speaker is off.
    assert row["presence"] == {
        "state": "unknown",
        "source": "none",
        "observed_at": None,
    }
    # And it claims no capabilities: the inventory knows what a body is for, not
    # what it can do right now.
    assert row["capabilities"] == []


def test_presence_is_never_overwritten_by_existence() -> None:
    joined = join_owner_devices(
        _snapshot(items=[_present("dev-living")]),
        devices=[_owned("dev-living", companion="companion-a")],
    )

    row = joined["devices"]["items"][0]
    # The blackboard answered for this one and stays the authority.
    assert row["presence"]["state"] == "online"
    assert row["presence"]["source"] == "runtime_blackboard"
    assert row["capabilities"] == ["voice.duplex"]
    # What it lacked, the inventory lent it.
    assert row["display_name"] == "esp-box-3"
    assert row["device_kind"] == "esp32-s3"
    assert row["companion_id"] == "companion-a"
    assert row["role_kind"] == "persona"


def test_one_body_each_side_makes_one_lane() -> None:
    joined = join_owner_devices(
        _snapshot(items=[_present("dev-living", display_name="esp-box-3")]),
        devices=[_owned("dev-study")],
    )

    lane = joined["devices"]
    assert {row["device_id"] for row in lane["items"]} == {"dev-living", "dev-study"}
    presence = {row["device_id"]: row["presence"]["state"] for row in lane["items"]}
    assert presence == {"dev-living": "online", "dev-study": "unknown"}


def test_no_inventory_leaves_the_lane_partly_known() -> None:
    joined = join_owner_devices(
        _snapshot(items=[_present("dev-living")]),
        devices=None,
        failure="设备清单读不到：Hub 没有回应",
    )

    lane = joined["devices"]
    # Presence answered, existence did not. Not ok — something is missing; not
    # unavailable — what was read is worth drawing.
    assert lane["state"] == "degraded"
    assert "Hub 没有回应" in lane["detail"]
    assert [row["device_id"] for row in lane["items"]] == ["dev-living"]


def test_no_presence_still_draws_the_bodies_that_exist() -> None:
    joined = join_owner_devices(
        _snapshot(state="unavailable", detail="运行黑板没有回应"),
        devices=[_owned("dev-living")],
    )

    lane = joined["devices"]
    assert lane["state"] == "degraded"
    assert "运行黑板" in lane["detail"]
    assert [row["device_id"] for row in lane["items"]] == ["dev-living"]
    assert lane["items"][0]["presence"]["state"] == "unknown"


def test_neither_side_answering_is_unavailable_and_empty() -> None:
    joined = join_owner_devices(
        _snapshot(state="unavailable", detail="运行黑板没有回应"),
        devices=None,
        failure="设备清单读不到",
    )

    lane = joined["devices"]
    assert lane["state"] == "unavailable"
    assert "运行黑板没有回应" in lane["detail"]
    assert "设备清单读不到" in lane["detail"]
    # An unreadable lane carries no rows.
    assert lane["items"] == []


def test_a_bound_body_reaches_the_map_bound() -> None:
    """The bug this file failed to catch, named.

    Kernel reported the assignment as ``Realized`` with
    ``effective_companion_id`` set; the Owner's device list showed the body; and
    every Eidolon on the star map read 身体未绑定. The join was reading
    ``answers_as_companion_id`` off the port's rows, which keep it at
    ``body.answering_companion_id`` — with ``getattr(..., None)``, so the
    mismatch became a plausible answer instead of an error.

    So this asserts the whole hop: a Companion bound in Kernel arrives on the
    map bound, through the same projection the device list is served in.
    """

    joined = join_owner_devices(
        _snapshot(),
        devices=[_owned("dev-box3", companion="cp_f943c383")],
    )

    row = joined["devices"]["items"][0]
    assert row["companion_id"] == "cp_f943c383"
    assert row["role_kind"] == "persona"
    assert row["role"] == "对话身体"
    # And a body nobody has pointed anywhere is still honestly unbound.
    unbound = join_owner_devices(
        _snapshot(),
        devices=[_owned("dev-spare")],
    )["devices"]["items"][0]
    assert unbound["companion_id"] is None
    assert unbound["role_kind"] == "unbound"


def test_the_join_refuses_a_shape_it_does_not_know() -> None:
    """Loudly, rather than defaulting into a wrong answer.

    The port's row and the Owner's projection are different shapes, and the join
    takes exactly one of them. Handed the other, it must fail here — where a
    person is looking — instead of drawing a nameless unbound body on a map.
    """

    port_shaped = SimpleNamespace(
        body=SimpleNamespace(answering_companion_id="cp_f943c383"),
    )

    with pytest.raises(AttributeError):
        join_owner_devices(_snapshot(), devices=[port_shaped])
