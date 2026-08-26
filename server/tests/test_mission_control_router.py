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

import importlib
import pathlib
import re

from datetime import UTC, datetime
from typing import Any

import pytest

from eidolon_admin_server.app.control_plane import clients
from eidolon_admin_server.app.mission_control import service
from eidolon_admin_server.app.mission_control.lanes import LaneLedger
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


async def test_a_removed_upstream_method_costs_one_lane_not_the_map() -> None:
    """The defect this file now guards, in its own words.

    ``_safe`` used to take an already-created awaitable, so
    ``client.gone_method(args)`` raised *before* ``_safe`` was entered. An
    upstream rename — the commonest drift there is — therefore bypassed the
    safety net entirely and took every lane with it. On 2026-08-26 the Hub
    client had lost ``list_devices`` and the whole composition answered 500,
    on a Host where six lanes were fine.

    Taking a callable is what fixes it, and this is the shape of the proof: a
    source whose method does not exist reports as a failed source, and the rest
    of the reading survives.
    """

    class _Gone:
        def __getattr__(self, name: str):
            raise AttributeError(f"'Gone' object has no attribute '{name}'")

    ledger = LaneLedger()
    result = await service._safe(
        ledger,
        "runtime.blackboard",
        lambda: _Gone().read_snapshot(owner_id="owner-1"),
        ["fallback"],
    )

    assert result == ["fallback"]
    status = next(
        row for row in ledger.statuses() if row.source == "runtime.blackboard"
    )
    assert status.ok is False
    assert "AttributeError" in status.detail
    # The failure is attributed, so it can reach a screen rather than a log.
    assert ledger.outcome("devices").state == "unavailable"
    assert "read_snapshot" in ledger.outcome("devices").detail


async def test_every_module_this_composition_imports_can_be_imported() -> None:
    """The third instance of one failure mode, so now it has a guard.

    Three capabilities this composition reached for had been removed upstream and
    the calls stayed: Hub's ``list_devices``, Hub's event feed, and
    ``app.memory.runners`` — the last deleted by
    ``06e7a2e align admin with data v2 and kernel boundary``. All three were
    invisible, two inside broad ``except`` blocks and one because ``_safe`` could
    not see a construction-time failure. For months the memory lane reported
    "No module named 'eidolon_admin_server.app.memory'" as though the memory
    service had been slow to answer.

    A function-level import is the one the type checker and the linter are least
    likely to notice, so it is the one worth asking about here: every module the
    composition names must exist.
    """

    source = pathlib.Path(service.__file__).read_text(encoding="utf-8")
    package = service.__name__.rsplit(".", 1)[0]
    modules = set(re.findall(r"^\s+from ([\w.]+) import ", source, re.MULTILINE))
    missing = []
    for module in sorted(modules):
        try:
            # Relative imports resolve against the composition's own package,
            # which is where they were written.
            importlib.import_module(module, package=package)
        except ImportError as exc:
            missing.append(f"{module} ({exc})")
    assert not missing, f"the composition imports modules that do not exist: {missing}"


async def test_the_retired_hub_capabilities_are_said_rather_than_asked_for() -> None:
    """Hub's management client no longer publishes an owner device page or an
    event feed, and this composition used to call both.

    It called them through ``_safe``, which did not help: the arguments were
    evaluated before ``_safe`` was entered, so a removed method raised there and
    took every lane with it — see ``test_a_removed_upstream_method_costs_one
    _lane`` below, and the operator console's map answering 500 for as long as
    nobody looked. What replaced them is a stated absence: nothing is asked for,
    and the reason is on the record where a reader can see it.
    """

    snapshot = await service.build_snapshot(
        _Request(_ControlPlane()), owner_id=_OWNER_ID
    )

    for source in ("hub.device_page", "hub.event_feed"):
        status = _status(snapshot, source)
        assert status is not None and status.ok is False, source
        assert status.detail, source


async def test_every_authority_method_this_composition_calls_really_exists() -> None:
    """The guard that was missing.

    ``test_devices_come_from_hub_which_is_the_device_authority`` used to stand
    here and passed against a stub that still had ``list_devices``. The real
    client had dropped it months earlier, and no test compared the two — so the
    composition was wrong in production and green in CI, which is the worst of
    the four combinations.

    So this reads the calls out of the composition and asks the real classes.
    """

    source = (
        pathlib.Path(service.__file__).read_text(encoding="utf-8")
    )
    authorities = {
        "data_authority_of": clients.DataAuthorityClient,
        "workspace_authority_of": clients.DataWorkspaceAuthorityClient,
        "hub_authority_of": clients.HubManagementClient,
    }
    missing: list[str] = []
    for helper, client in authorities.items():
        for method in set(
            re.findall(rf"{helper}\(control_plane\)\.([a-z_]+)\(", source)
        ):
            if not hasattr(client, method):
                missing.append(f"{client.__name__}.{method}")
    assert not missing, (
        "the composition calls authority methods that do not exist: "
        f"{sorted(missing)}"
    )


async def test_an_admin_with_no_control_plane_says_that_and_stops() -> None:
    snapshot = await service.build_snapshot(_Request(None), owner_id=_OWNER_ID)

    status = _status(snapshot, "control-plane")
    assert status is not None and status.ok is False
    assert "no control-plane clients" in status.detail


async def test_a_live_event_is_returned_as_it_arrived() -> None:
    """The enrichment this used to assert has been dead for a while.

    It resolved a device's Companion through Hub's owner device page — a method
    the Hub management client no longer has — inside a bare ``except``. So it
    silently did nothing, while a test built on a stub that still had the method
    said it worked. The call is gone now, and this holds what actually happens:
    an event crosses unchanged.

    The owner-isolated runtime blackboard carries the device→Companion binding
    and is where this belongs if it is wanted again. Nothing on the Owner's map
    needs it: their events come from the audit index, which records the subject
    at the time it happened.
    """

    from eidolon_admin_server.app.mission_control.schemas import RuntimeEvent

    request = _Request(_ControlPlane(devices=[_Device("dev-a", companion_id="cmp-1")]))

    def event(**fields: Any) -> RuntimeEvent:
        return RuntimeEvent(
            ts=datetime.now(UTC),
            source="hub",
            type="device.presence",
            summary="",
            **fields,
        )

    for row in (
        event(event_id="e1", device_id="dev-a"),
        event(event_id="e2", device_id="dev-a", owner_id=_OWNER_ID),
    ):
        crossed = await service.enrich_runtime_event(request, row)
        assert crossed.companion_id is None
        assert crossed.event_id == row.event_id


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


async def test_a_reconnecting_reader_resumes_where_it_stopped() -> None:
    """The cursor is the protocol's own, and it has to be honoured.

    Before this the tail started at "now": a dropped connection meant everything
    that happened while it was down existed nowhere in the stream, and the
    periodic snapshot was the only backstop — but a snapshot is a state, not the
    events that produced it. §11.2 forbids exactly this.
    """

    from eidolon_admin_server.app.mission_control.router import _cursor

    resumed = _cursor("2026-08-25T09:00:00+00:00")
    assert resumed is not None
    assert resumed.tzinfo is not None
    # A first connection has nothing to resume from, and says so with None
    # rather than with a date that would silently skip history.
    assert _cursor(None) is None
    assert _cursor("   ") is None
    # A cursor from another version of this stream gets a live connection rather
    # than an error it cannot act on: it loses the gap, which it would have lost
    # anyway.
    assert _cursor("not-a-position") is None


async def test_only_the_source_with_a_position_stamps_a_frame() -> None:
    """An id that does not mean a position would overwrite one that does.

    The stream merges a proxied Hub substream with the audit tail. Only the tail
    knows where it is, so only its frames carry an id — otherwise a reconnecting
    reader would resume from a Hub frame it cannot look up.
    """

    from eidolon_sdk.core.streaming import encode_sse_event

    stamped = encode_sse_event(
        "runtime_event", {"event_id": "a"}, event_id="2026-08-25T09:00:00+00:00"
    )
    unstamped = encode_sse_event("runtime_event", {"event_id": "b"})

    assert stamped.startswith(b"id: 2026-08-25T09:00:00+00:00\n")
    assert not unstamped.startswith(b"id:")
