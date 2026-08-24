"""What the cockpit says when an authority cannot answer it.

Mission Control used to open the product database and read owners, companions,
devices, conversations, jobs and Guard bindings straight out of it. Admin does
not do that any more — Data owns owners and companions, Hub owns devices and
their events, and an architecture test enforces the boundary. Restoring this
surface meant rewriting how it gets its data, and half of what it used to show
turned out to have no HTTP authority answering for it at all.

That half is the subject here. A panel with nothing in it because the Host is
quiet and a panel with nothing in it because nobody publishes the data look
identical, and only one of them is a fault. These tests exist to keep the two
apart.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from eidolon_admin_server.app.mission_control import service
from eidolon_admin_server.app.mission_control.schemas import SourceStatus

pytestmark = pytest.mark.asyncio

_OWNER_ID = "owner-1"


class _Owner:
    """What the workspace authority actually publishes — no more.

    Deliberately not the old database row: that had a `kind` and a `status`
    this contract does not carry, and a fake richer than the real answer would
    let the code read fields production never sees.
    """

    operation = "owner.identity"
    owner_id = _OWNER_ID
    display_name = "曼森"
    #: The pointer. It is the only thing that says which Companion is the
    #: default, so the fake carries it and nothing else infers it.
    default_companion_id: str | None = None
    lifecycle_state = "active"


class _Device:
    def __init__(self, device_id: str, companion_id: str | None = None) -> None:
        self.device_id = device_id
        self.owner_id = _OWNER_ID
        self.bound_companion_id = companion_id
        self.display_name = device_id
        self.status = "active"


class _DevicePage:
    def __init__(self, devices: list[_Device]) -> None:
        self.devices = devices


class _WorkspaceAuthority:
    def __init__(self, default_companion_id: str | None = None) -> None:
        self._default_companion_id = default_companion_id

    async def get_owner(self, owner_id: str) -> _Owner:
        assert owner_id == _OWNER_ID
        owner = _Owner()
        owner.default_companion_id = self._default_companion_id
        return owner


class _DataAuthority:
    def __init__(self, snapshot: Any = None) -> None:
        self._snapshot = snapshot

    async def get_owner_default_runtime(self, owner_id: str) -> Any:
        return self._snapshot


class _HubManagement:
    def __init__(self, devices: list[_Device] | None = None) -> None:
        self._devices = devices or []

    async def list_devices(self, owner_id: str, **_: Any) -> _DevicePage:
        return _DevicePage(self._devices)

    async def list_events(self, owner_id: str, **_: Any) -> list[Any]:
        return []


class _ControlPlane:
    """Shaped like the real ControlPlaneService, which it was not.

    This stub used to expose ``workspace_authority`` / ``data_authority`` /
    ``hub_management``. The composed service has ``workspace`` / ``data`` /
    ``hub``, so these tests were green while the cockpit answered every real
    request with an empty snapshot. The attribute names here are the contract
    now, and test_mission_control_composition.py checks them against the real
    thing.
    """

    def __init__(
        self,
        devices: list[_Device] | None = None,
        *,
        snapshot: Any = None,
        default_companion_id: str | None = None,
    ) -> None:
        self.workspace = _WorkspaceAuthority(default_companion_id)
        self.data = _DataAuthority(snapshot)
        self.hub = _HubManagement(devices)


class _State:
    def __init__(self, control_plane: Any) -> None:
        self.control_plane = control_plane


class _App:
    def __init__(self, control_plane: Any) -> None:
        self.state = _State(control_plane)


class _Request:
    def __init__(self, control_plane: Any) -> None:
        self.app = _App(control_plane)


def _status(snapshot: Any, source: str) -> SourceStatus | None:
    return next((s for s in snapshot.source_status if s.source == source), None)


async def test_a_lane_no_authority_answers_says_so_rather_than_reading_empty() -> None:
    snapshot = await service.build_snapshot(_Request(_ControlPlane()), owner_id=_OWNER_ID)

    # Four lanes the database version could fill and no HTTP authority can.
    # Each is reported unavailable with the reason, so an operator looking at an
    # empty panel knows whether to worry.
    for source, expected in {
        "data.conversations": "no conversation history",
        "data.jobs": "no job list",
        "data.guard_bindings": "Guard runtime does not exist",
        "data.memory": "not the realm roster",
    }.items():
        status = _status(snapshot, source)
        assert status is not None, f"{source} vanished instead of reporting itself"
        assert status.ok is False
        assert expected in status.detail

    # And the payload really is empty — the point is the status beside it, not
    # a fabricated filler row.
    assert snapshot.jobs == []


async def test_a_snapshot_without_an_owner_refuses_rather_than_picking_one() -> None:
    """No authority publishes an Owner list, so there is nobody to choose from.

    The database version listed every Owner and took the first active one. Doing
    that now would mean inventing the answer, and on a Host with more than one
    Owner it would silently show the wrong person's devices.
    """

    snapshot = await service.build_snapshot(_Request(_ControlPlane()), owner_id=None)

    status = _status(snapshot, "data.owners")
    assert status is not None and status.ok is False
    assert "ask for one Owner by id" in status.detail
    assert snapshot.owner is None


async def test_devices_come_from_hub_which_is_the_device_authority() -> None:
    control_plane = _ControlPlane(devices=[_Device("dev-a"), _Device("dev-b")])

    snapshot = await service.build_snapshot(_Request(control_plane), owner_id=_OWNER_ID)

    assert _status(snapshot, "hub.devices").ok is True
    assert {device.device_id for device in snapshot.devices} >= {"dev-a", "dev-b"}


async def test_an_admin_with_no_control_plane_says_that_and_stops() -> None:
    snapshot = await service.build_snapshot(_Request(None), owner_id=_OWNER_ID)

    status = _status(snapshot, "control-plane")
    assert status is not None and status.ok is False
    assert "no control-plane clients" in status.detail


async def test_an_event_is_enriched_from_hub_only_when_it_says_whose_it_is() -> None:
    """Hub answers per Owner, so an event with no Owner cannot be resolved.

    Returning it unchanged is the honest outcome; guessing an Owner would
    attribute one person's device activity to another.
    """

    from eidolon_admin_server.app.mission_control.schemas import RuntimeEvent

    control_plane = _ControlPlane(devices=[_Device("dev-a", companion_id="cmp-1")])
    request = _Request(control_plane)

    def event(**fields: Any) -> RuntimeEvent:
        return RuntimeEvent(
            ts=datetime.now(UTC),
            source="hub",
            type="device.presence",
            summary="",
            **fields,
        )

    anonymous = event(event_id="e1", device_id="dev-a")
    assert (await service.enrich_runtime_event(request, anonymous)).companion_id is None

    owned = event(event_id="e2", device_id="dev-a", owner_id=_OWNER_ID)
    assert (await service.enrich_runtime_event(request, owned)).companion_id == "cmp-1"


async def test_the_default_companion_is_the_one_the_owner_points_at() -> None:
    """Not "the first active row", which is what this used to pick.

    With one Companion those agree, which is why the old code looked fine. The
    pointer is the only thing that decides, and this projection now reads it
    instead of choosing.
    """

    class _Companion:
        companion_id = "cmp-1"
        display_name = "小忆"
        kind = "conversational"
        lifecycle_state = "active"
        current_genome_id = "g-1"

    class _Snapshot:
        companion = _Companion()

    control_plane = _ControlPlane(
        snapshot=_Snapshot(), default_companion_id="cmp-1"
    )
    snapshot = await service.build_snapshot(
        _Request(control_plane), owner_id=_OWNER_ID
    )

    assert snapshot.default_companion_id == "cmp-1"
    assert snapshot.companion is not None
    assert snapshot.companion.companion_id == "cmp-1"
    # Read from the column it comes from, so it is no longer always empty.
    assert snapshot.companion.lifecycle_state == "active"
    # And no row carries a flag that could contradict the pointer.
    assert not hasattr(snapshot.companions[0], "is_master")


async def test_an_owner_with_no_default_is_shown_as_having_none() -> None:
    """A real state — every Companion archived, or only a guard.

    Picking a row anyway would make the cockpit disagree with every other
    surface about which Eidolon answers.
    """

    class _Companion:
        companion_id = "cmp-1"
        display_name = "守卫"
        kind = "guard"
        lifecycle_state = "active"

    class _Snapshot:
        companion = _Companion()

    control_plane = _ControlPlane(snapshot=_Snapshot(), default_companion_id=None)
    snapshot = await service.build_snapshot(
        _Request(control_plane), owner_id=_OWNER_ID
    )

    assert snapshot.default_companion_id is None
    assert snapshot.companion is None
    assert [row.companion_id for row in snapshot.companions] == ["cmp-1"]
