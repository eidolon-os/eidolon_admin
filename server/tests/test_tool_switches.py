"""The Owner's tool switches through Admin: views, checks, refusals, and one device.

What these hold:

- the views compose the Agent's catalog with both layers of Data's switches
  through the SDK's one rule, and a Companion's own value survives the Owner's
  switch being off;
- a change is checked against the catalog before Data hears of it;
- Data's 409 reaches the phone as it was said, code included;
- a Companion that is not this Owner's is absent, read or written;
- a device's reasons translate the Channel Provider's offer, never re-derive it.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from eidolon_sdk.biz.tools import (
    CompanionToolPolicy,
    CompanionToolPolicyState,
    DeviceToolOffer,
    DeviceToolRestriction,
    OwnerToolPolicyState,
    ToolCatalog,
    ToolCatalogEntry,
    ToolPolicy,
    ToolPolicyChange,
    ToolRequirements,
    ToolReservation,
    apply_changes,
)
from fastapi import FastAPI, HTTPException

from eidolon_admin_server.app.control_plane.clients import (
    DATA_CONTRACT,
    AgentActivityClient,
    DataAuthorityClient,
)
from eidolon_admin_server.app.control_plane.contracts import (
    CompanionRosterPage,
    CompanionSummary,
)
from eidolon_admin_server.app.control_plane.errors import AuthorityFailure
from eidolon_admin_server.app.control_plane.failure_handler import (
    install_authority_failure_handler,
)
from eidolon_admin_server.app.management.activity_feed import read_activity
from eidolon_admin_server.app.management.device_settings_router import DeviceOffers
from eidolon_admin_server.app.management.tools import ToolGovernance, device_reasons
from eidolon_admin_server.app.management.tools_router import router as tools_router
from eidolon_admin_server.local_api.management.backend import AdminManagementClient
from tests.test_control_plane_clients import StaticDirectory
from tests.test_management_activity_feed import _Companions, _event, _History
from tests.test_management_devices import (
    _KNOWN,
    _app,
    _authenticate,
    _device,
    _Devices,
    _stub_controller,
)

_MADE = datetime(2026, 11, 15, 9, 0, tzinfo=UTC)
_SECRET = "local-api-secret"


def _entry(key: str, group: str, kind: str, **fields) -> ToolCatalogEntry:
    return ToolCatalogEntry(
        key=key, label=f"{key} 名称", summary=f"{key} 说明", group=group, kind=kind,
        side_effect=fields.pop("side_effect", False), **fields,
    )


def _catalog(*, vision_host: bool = True) -> ToolCatalog:
    return ToolCatalog.of([
        _entry("get_weather", "general", "brain"),
        _entry("get_time", "general", "brain"),
        _entry("delegate_to_coworker", "collaboration", "brain",
               reserved=ToolReservation(reason="interactive_session")),
        _entry("music.play", "music", "channel", side_effect=True,
               requires=ToolRequirements(outputs=("speech",), host_feature="music")),
        _entry("motion.nod", "motion", "device", side_effect=True, body_control=True,
               requires=ToolRequirements(outputs=("motion",), device_contracts=("motion.nod",))),
        _entry("stackchan_head", "motion", "agent_device", side_effect=True, body_control=True,
               requires=ToolRequirements(outputs=("motion",), motion_profile="stackchan.head.v1")),
        _entry("vision.look", "vision", "agent_device", host_available=vision_host,
               requires=ToolRequirements(inputs={"camera": "capture"},
                                         device_contracts=("camera.capture",),
                                         host_feature="vision_model")),
        _entry("vision.who", "vision", "channel",
               requires=ToolRequirements(inputs={"camera": "capture"})),
        _entry("audio.volume.set", "device_settings", "device", side_effect=True, body_control=True,
               requires=ToolRequirements(outputs=("speech",), device_contracts=("audio.volume.set",))),
    ])


CATALOG = _catalog()


def _summary(companion_id: str, name: str) -> CompanionSummary:
    return CompanionSummary(
        companion_id=companion_id, display_name=name, kind="conversational",
        lifecycle_state="active", revision=1, created_at=_MADE, updated_at=_MADE,
    )


class Agent:
    def __init__(self, catalog: ToolCatalog = CATALOG) -> None:
        self.catalog, self.reads = catalog, 0

    async def tool_catalog(self) -> ToolCatalog:
        self.reads += 1
        return self.catalog


class Data:
    """Data's switches and roster for ``owner-1``, in memory, every call recorded."""

    def __init__(self) -> None:
        self.owner = ToolPolicy()
        self.companions = {
            "companion-a": CompanionToolPolicy(),
            "companion-b": CompanionToolPolicy(overrides={"get_weather": False}),
        }
        #: Two roster pages; ``companion-x`` is kept by Data but on no page.
        self.pages = [
            (("companion-a", "小忆"),),
            (("companion-b", "阿力"),),
        ]
        self.calls: list[tuple] = []

    def _state(self) -> OwnerToolPolicyState:
        return OwnerToolPolicyState(
            owner_id="owner-1", policy=self.owner,
            companions=tuple(
                CompanionToolPolicyState(companion_id=cid, lifecycle_state="active", policy=policy)
                for cid, policy in sorted(self.companions.items())
            ),
        )

    async def list_owner_companions(self, owner_id, *, cursor=None, limit=None):
        self.calls.append(("roster", owner_id, cursor))
        index = int(cursor or 0)
        return CompanionRosterPage(
            contract_version="1", operation="companion.roster-page", owner_id=owner_id,
            companions=tuple(_summary(cid, name) for cid, name in self.pages[index]),
            next_cursor=str(index + 1) if index + 1 < len(self.pages) else None,
        )

    async def get_owner_companion(self, owner_id, companion_id):
        self.calls.append(("ownership", owner_id, companion_id))
        if owner_id != "owner-1" or companion_id not in self.companions:
            raise AuthorityFailure("data", "not_found", "companion not found", 404, 404)
        return SimpleNamespace(companion_id=companion_id, owner_id=owner_id)

    async def owner_tool_policy(self, owner_id):
        self.calls.append(("owner", owner_id))
        return self._state()

    async def change_owner_tool_policy(self, owner_id, change):
        self.calls.append(("change-owner", owner_id, change))
        if change.expected_revision != self.owner.revision:
            raise AuthorityFailure("data", "conflict", "the tool policy was changed meanwhile",
                                   409, 409, code="tool_policy_changed")
        self.owner = apply_changes(self.owner, change)
        return self._state()

    async def companion_tool_policy(self, companion_id):
        self.calls.append(("companion", companion_id))
        return CompanionToolPolicyState(
            companion_id=companion_id, lifecycle_state="active", policy=self.companions[companion_id]
        )

    async def change_companion_tool_policy(self, companion_id, change):
        self.calls.append(("change-companion", companion_id, change))
        current = self.companions[companion_id]
        if change.expected_revision != current.revision:
            raise AuthorityFailure("data", "conflict", "the tool policy was changed meanwhile",
                                   409, 409, code="tool_policy_changed")
        self.companions[companion_id] = apply_changes(current, change)
        return await self.companion_tool_policy(companion_id)

    @property
    def writes(self) -> list[tuple]:
        return [call for call in self.calls if call[0].startswith("change-")]


class Offers:
    def __init__(self, offer: DeviceToolOffer | None) -> None:
        self.offer_value, self.asked = offer, []

    async def offer(self, *, owner_id, device_id):
        self.asked.append((owner_id, device_id))
        return self.offer_value


def _governance(agent=None, data=None, offers=None):
    agent, data = agent or Agent(), data or Data()
    return ToolGovernance(catalog=agent, policies=data, roster=data, offers=offers or Offers(None)), agent, data


def _row(view, key: str):
    return next(tool for tool in view.tools if tool.key == key)


# ----------------------------------------------------------------------------- the overview


async def test_the_owners_switch_off_turns_every_companion_off_and_keeps_their_own_values():
    governance, _, data = _governance()
    data.owner = ToolPolicy(revision=2, overrides={"get_weather": False})

    view = await governance.overview("owner-1")

    weather = _row(view, "get_weather")
    assert view.revision == 2
    assert view.catalog_revision == CATALOG.catalog_revision
    assert (weather.global_enabled, weather.global_overridden) == (False, True)
    assert [(c.companion_id, c.name, c.enabled, c.effective) for c in weather.companions] == [
        ("companion-a", "小忆", True, False),
        ("companion-b", "阿力", False, False),
    ]
    # Untouched tools read as their defaults, and every Companion has them.
    clock = _row(view, "get_time")
    assert (clock.global_enabled, clock.global_overridden) == (True, False)
    assert [c.effective for c in clock.companions] == [True, True]


async def test_turning_the_owners_switch_back_on_restores_each_companions_choice():
    governance, _, data = _governance()
    data.owner = ToolPolicy(revision=1, overrides={"get_weather": False})

    view = await governance.change_overview(
        "owner-1", ToolPolicyChange(expected_revision=1, changes={"get_weather": None})
    )

    weather = _row(view, "get_weather")
    assert view.revision == 2
    assert (weather.global_enabled, weather.global_overridden) == (True, False)
    assert [(c.enabled, c.effective) for c in weather.companions] == [(True, True), (False, False)]


async def test_tools_are_ordered_by_group_then_key_with_reserved_tools_last():
    governance, _, _ = _governance()

    view = await governance.overview("owner-1")

    assert [tool.key for tool in view.tools] == [
        "audio.volume.set", "get_time", "get_weather", "motion.nod", "stackchan_head",
        "music.play", "vision.look", "vision.who", "delegate_to_coworker",
    ]
    reserved = _row(view, "delegate_to_coworker")
    assert reserved.reserved == ToolReservation(reason="interactive_session")
    assert not any(c.effective for c in reserved.companions)


async def test_companion_names_come_from_every_roster_page_and_only_named_companions_show():
    governance, _, data = _governance()
    data.companions["companion-x"] = CompanionToolPolicy()

    view = await governance.overview("owner-1")

    assert [c.companion_id for c in _row(view, "get_time").companions] == ["companion-a", "companion-b"]
    assert [call for call in data.calls if call[0] == "roster"] == [
        ("roster", "owner-1", None), ("roster", "owner-1", "1"),
    ]


# ----------------------------------------------------------------------------- one Companion


async def test_a_companion_view_says_which_layer_keeps_each_tool():
    governance, _, data = _governance()
    data.owner = ToolPolicy(revision=1, overrides={"get_time": False})
    data.companions["companion-a"] = CompanionToolPolicy(
        revision=4, allow_body_control=False, overrides={"music.play": False, "motion.nod": True}
    )

    view = await governance.companion("owner-1", "companion-a")

    assert (view.companion_id, view.revision, view.allow_body_control) == ("companion-a", 4, False)
    blocked = {tool.key: tool.blocked_by for tool in view.tools}
    assert blocked == {
        "delegate_to_coworker": "reserved",
        "get_time": "global",
        "motion.nod": "body_control",
        "stackchan_head": "body_control",
        "audio.volume.set": "body_control",
        "music.play": "companion",
        "get_weather": None,
        "vision.look": None,
        "vision.who": None,
    }
    nod = _row(view, "motion.nod")
    # The body switch is off; the Companion's own choice for the tool is kept.
    assert (nod.companion_enabled, nod.companion_overridden, nod.effective, nod.body_control) == (
        True, True, False, True)
    clock = _row(view, "get_time")
    assert (clock.global_enabled, clock.companion_enabled, clock.effective) == (False, True, False)
    assert all(tool.effective == (tool.blocked_by is None) for tool in view.tools)


async def test_another_owners_companion_is_absent_whether_read_or_written():
    governance, _, data = _governance()

    with pytest.raises(AuthorityFailure) as read:
        await governance.companion("owner-1", "companion-elsewhere")
    with pytest.raises(AuthorityFailure) as written:
        await governance.change_companion(
            "owner-1", "companion-elsewhere",
            ToolPolicyChange(expected_revision=0, changes={"get_weather": False}),
        )

    assert (read.value.status_code, written.value.status_code) == (404, 404)
    assert not [call for call in data.calls if call[0] in {"companion", "change-companion"}]


async def test_a_companion_change_returns_the_composed_view():
    governance, _, data = _governance()

    view = await governance.change_companion(
        "owner-1", "companion-a",
        ToolPolicyChange(expected_revision=0, changes={"vision.look": False}, allow_body_control=False),
    )

    assert (view.revision, view.allow_body_control) == (1, False)
    assert _row(view, "vision.look").blocked_by == "companion"
    assert data.companions["companion-a"].overrides == {"vision.look": False}


# ----------------------------------------------------------------------------- checks before Data


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"teleport": True}, "unknown_tool"),
        ({"get_weather": False, "teleport": None}, "unknown_tool"),
        ({"delegate_to_coworker": True}, "reserved_tool"),
        ({"delegate_to_coworker": None}, "reserved_tool"),
    ],
)
async def test_a_change_the_catalog_does_not_allow_never_reaches_data(changes, code):
    governance, _, data = _governance()
    change = ToolPolicyChange(expected_revision=0, changes=changes)

    for attempt in (
        governance.change_overview("owner-1", change),
        governance.change_companion("owner-1", "companion-a", change),
    ):
        with pytest.raises(AuthorityFailure) as refused:
            await attempt
        assert (refused.value.status_code, refused.value.code) == (422, code)
    assert data.calls == []


async def test_the_body_switch_is_a_companions_and_not_the_owners():
    governance, agent, data = _governance()

    with pytest.raises(AuthorityFailure) as refused:
        await governance.change_overview(
            "owner-1", ToolPolicyChange(expected_revision=0, allow_body_control=False)
        )

    assert refused.value.status_code == 422
    assert data.calls == [] and agent.reads == 0


async def test_a_stale_revision_is_datas_409_unchanged():
    governance, _, data = _governance()
    data.owner = ToolPolicy(revision=3)

    with pytest.raises(AuthorityFailure) as refused:
        await governance.change_overview(
            "owner-1", ToolPolicyChange(expected_revision=2, changes={"get_weather": False})
        )

    assert (refused.value.status_code, refused.value.code, refused.value.authority) == (
        409, "tool_policy_changed", "data")


# ----------------------------------------------------------------------------- one device

BOX3 = DeviceToolOffer(
    device_id="box3", declared_capabilities=("audio.volume.set",), declared_motion_profile=None,
    policy_revision=2, outputs={"speech": True, "expression": True},
    session_capabilities=("audio.volume.set",),
)
STACKCHAN_CAMERA_OFF = DeviceToolOffer(
    device_id="stackchan", declared_capabilities=("audio.volume.set", "camera.capture"),
    declared_motion_profile="stackchan.head.v1", policy_revision=3,
    outputs={"speech": True, "motion": True}, session_capabilities=("audio.volume.set",),
)
STACKCHAN_STILL = STACKCHAN_CAMERA_OFF.model_copy(update={
    "outputs": {"speech": True, "motion": False},
    "session_capabilities": ("audio.volume.set", "camera.capture"),
})
STACKCHAN_ALL = STACKCHAN_CAMERA_OFF.model_copy(update={
    "session_capabilities": ("audio.volume.set", "camera.capture"),
})
REACHY = DeviceToolOffer(
    device_id="reachy", declared_capabilities=("motion.nod", "camera.capture"),
    declared_motion_profile="reachy.v1", policy_revision=1,
    outputs={"speech": True, "motion": True},
    session_capabilities=("motion.nod", "camera.capture"),
)
UNDECIDED = DeviceToolOffer(device_id="new", declared_capabilities=("audio.volume.set",))

_OPEN = CompanionToolPolicy()


@pytest.mark.parametrize(
    ("key", "owner", "companion", "offer", "catalog", "expected"),
    [
        ("delegate_to_coworker", ToolPolicy(), _OPEN, BOX3, CATALOG, ["reserved"]),
        ("delegate_to_coworker", ToolPolicy(), None, None, CATALOG, ["reserved"]),
        ("get_weather", ToolPolicy(), _OPEN, None, CATALOG, []),
        ("get_weather", ToolPolicy(overrides={"get_weather": False}), _OPEN, BOX3, CATALOG,
         ["global_off"]),
        ("get_weather", ToolPolicy(overrides={"get_weather": False}), None, BOX3, CATALOG,
         ["global_off", "no_companion"]),
        ("get_weather", ToolPolicy(), None, BOX3, CATALOG, ["no_companion"]),
        ("get_weather", ToolPolicy(), CompanionToolPolicy(overrides={"get_weather": False}), BOX3,
         CATALOG, ["companion_off"]),
        ("motion.nod", ToolPolicy(), CompanionToolPolicy(allow_body_control=False), REACHY, CATALOG,
         ["body_control_off"]),
        # A no-companion device skips the Companion's layers.
        ("motion.nod", ToolPolicy(), None, REACHY, CATALOG, ["no_companion"]),
        ("motion.nod", ToolPolicy(), _OPEN, REACHY, CATALOG, []),
        ("motion.nod", ToolPolicy(), _OPEN, BOX3, CATALOG,
         ["device_not_declared:motion.nod", "output_not_allowed:motion"]),
        # A BOX-3: no head, no camera.
        ("stackchan_head", ToolPolicy(), _OPEN, BOX3, CATALOG,
         ["motion_profile_mismatch", "output_not_allowed:motion"]),
        ("vision.look", ToolPolicy(), _OPEN, BOX3, CATALOG, ["device_not_declared:camera.capture"]),
        ("vision.who", ToolPolicy(), _OPEN, BOX3, CATALOG, ["device_not_declared:camera.capture"]),
        ("audio.volume.set", ToolPolicy(), _OPEN, BOX3, CATALOG, []),
        ("music.play", ToolPolicy(), _OPEN, BOX3, CATALOG, []),
        # A StackChan with its camera off: it can, the Owner has not let it.
        ("vision.look", ToolPolicy(), _OPEN, STACKCHAN_CAMERA_OFF, CATALOG, ["input_not_allowed:camera"]),
        ("vision.who", ToolPolicy(), _OPEN, STACKCHAN_CAMERA_OFF, CATALOG, ["input_not_allowed:camera"]),
        ("stackchan_head", ToolPolicy(), _OPEN, STACKCHAN_CAMERA_OFF, CATALOG, []),
        # Motion not selected.
        ("stackchan_head", ToolPolicy(), _OPEN, STACKCHAN_STILL, CATALOG, ["output_not_allowed:motion"]),
        ("vision.look", ToolPolicy(), _OPEN, STACKCHAN_ALL, CATALOG, []),
        # No channel yet, and outputs never decided.
        ("music.play", ToolPolicy(), _OPEN, None, CATALOG, ["channel_not_ready"]),
        ("stackchan_head", ToolPolicy(), None, None, CATALOG, ["no_companion", "channel_not_ready"]),
        ("music.play", ToolPolicy(), _OPEN, UNDECIDED, CATALOG, ["output_not_allowed:speech"]),
        # The Host itself is missing what the tool needs.
        ("vision.look", ToolPolicy(), _OPEN, STACKCHAN_ALL, _catalog(vision_host=False),
         ["host_not_configured:vision_model"]),
        ("vision.look", ToolPolicy(overrides={"vision.look": False}), None, BOX3,
         _catalog(vision_host=False),
         ["global_off", "no_companion", "device_not_declared:camera.capture",
          "host_not_configured:vision_model"]),
    ],
)
def test_device_reasons(key, owner, companion, offer, catalog, expected):
    entry = catalog.entry(key)

    assert device_reasons(
        entry, owner=owner, companion=companion, offer=offer, restriction=None
    ) == expected


RESTRICTED = DeviceToolRestriction(
    revision=2,
    denied=("audio.volume.set", "delegate_to_coworker", "get_weather", "stackchan_head", "vision.look"),
)
#: What the Provider grants a BOX-3 whose volume contract is restricted.
BOX3_RESTRICTED = BOX3.model_copy(update={"session_capabilities": ()})


@pytest.mark.parametrize(
    ("key", "owner", "companion", "offer", "restriction", "catalog", "expected"),
    [
        ("get_weather", ToolPolicy(), _OPEN, BOX3, RESTRICTED, CATALOG, ["device_restricted"]),
        # Right after the switches' reasons, before what the device can do.
        ("get_weather", ToolPolicy(overrides={"get_weather": False}), None, BOX3, RESTRICTED, CATALOG,
         ["global_off", "no_companion", "device_restricted"]),
        ("get_weather", ToolPolicy(), CompanionToolPolicy(overrides={"get_weather": False}), BOX3,
         RESTRICTED, CATALOG, ["companion_off", "device_restricted"]),
        ("stackchan_head", ToolPolicy(), CompanionToolPolicy(allow_body_control=False), BOX3,
         RESTRICTED, CATALOG,
         ["body_control_off", "device_restricted", "motion_profile_mismatch", "output_not_allowed:motion"]),
        ("audio.volume.set", ToolPolicy(), _OPEN, BOX3_RESTRICTED, RESTRICTED, CATALOG,
         ["device_restricted"]),
        ("audio.volume.set", ToolPolicy(), _OPEN, None, RESTRICTED, CATALOG,
         ["device_restricted", "channel_not_ready"]),
        ("vision.look", ToolPolicy(), _OPEN, STACKCHAN_ALL, RESTRICTED, CATALOG, ["device_restricted"]),
        ("vision.look", ToolPolicy(), _OPEN, STACKCHAN_ALL, RESTRICTED, _catalog(vision_host=False),
         ["device_restricted", "host_not_configured:vision_model"]),
        # Reserved stays the only reason; a tool nobody restricted is untouched.
        ("delegate_to_coworker", ToolPolicy(), _OPEN, BOX3, RESTRICTED, CATALOG, ["reserved"]),
        ("music.play", ToolPolicy(), _OPEN, BOX3, RESTRICTED, CATALOG, []),
        # A restriction listing nothing withholds nothing.
        ("get_weather", ToolPolicy(), _OPEN, BOX3, DeviceToolRestriction(revision=3), CATALOG, []),
    ],
)
def test_device_reasons_with_a_restriction(key, owner, companion, offer, restriction, catalog, expected):
    entry = catalog.entry(key)

    assert device_reasons(
        entry, owner=owner, companion=companion, offer=offer, restriction=restriction
    ) == expected


def test_a_declared_contract_the_channel_still_withholds_is_logged_not_invented(caplog):
    # Requirements met and declared, yet not offered: the Provider and the
    # catalog disagree, which is for an operator, not a reason for the phone.
    with caplog.at_level(logging.WARNING, logger="eidolon_admin_server.app.management.tools"):
        reasons = device_reasons(
            CATALOG.entry("audio.volume.set"), owner=ToolPolicy(), companion=_OPEN,
            offer=BOX3_RESTRICTED, restriction=None,
        )

    assert reasons == []
    assert "does not offer it" in caplog.text


def test_a_restricted_contract_missing_from_the_session_is_expected_not_logged(caplog):
    # The grant leaves restricted contracts out; that is the restriction working.
    with caplog.at_level(logging.WARNING, logger="eidolon_admin_server.app.management.tools"):
        reasons = device_reasons(
            CATALOG.entry("audio.volume.set"), owner=ToolPolicy(), companion=_OPEN,
            offer=BOX3_RESTRICTED, restriction=RESTRICTED,
        )

    assert reasons == ["device_restricted"]
    assert caplog.text == ""


async def test_a_device_view_reads_the_offer_once_and_uses_the_bound_companions_switches():
    offers = Offers(STACKCHAN_CAMERA_OFF)
    governance, _, data = _governance(offers=offers)
    data.companions["companion-a"] = CompanionToolPolicy(allow_body_control=False)

    view = await governance.device("owner-1", "stackchan", "companion-a", None)

    assert offers.asked == [("owner-1", "stackchan")]
    assert (view.device_id, view.companion_id, view.restriction) == ("stackchan", "companion-a", None)
    head = _row(view, "stackchan_head")
    assert (head.available, head.reasons) == (False, ["body_control_off"])
    weather = _row(view, "get_weather")
    assert (weather.available, weather.reasons) == (True, [])
    assert _row(view, "vision.look").reasons == ["input_not_allowed:camera"]


async def test_a_device_without_a_companion_says_so_on_every_tool_it_could_use():
    governance, _, _ = _governance(offers=Offers(BOX3))

    view = await governance.device("owner-1", "box3", None, None)

    assert view.companion_id is None
    assert _row(view, "get_time").reasons == ["no_companion"]
    assert _row(view, "delegate_to_coworker").reasons == ["reserved"]


async def test_a_device_view_carries_its_restriction_and_says_what_it_withholds():
    governance, _, _ = _governance(offers=Offers(BOX3_RESTRICTED))

    view = await governance.device("owner-1", "box3", "companion-a", RESTRICTED)

    assert view.restriction == RESTRICTED
    assert _row(view, "audio.volume.set").reasons == ["device_restricted"]
    assert _row(view, "get_weather").reasons == ["device_restricted"]
    assert (_row(view, "get_time").available, _row(view, "music.play").available) == (True, True)


async def test_a_bound_companion_the_owner_does_not_keep_is_a_disagreement_not_defaults():
    governance, _, _ = _governance(offers=Offers(BOX3))

    with pytest.raises(AuthorityFailure) as refused:
        await governance.device("owner-1", "box3", "companion-gone", None)

    assert refused.value.status_code == 409


# ----------------------------------------------------------------------------- the clients


def _data_client(handler) -> DataAuthorityClient:
    return DataAuthorityClient(
        directory=StaticDirectory({("data", "companion-authority.http"): ("http://data.test", DATA_CONTRACT)}),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        service_token="data-token", timeout_seconds=1,
    )


async def test_the_data_client_reads_and_changes_both_layers():
    seen = []
    owner_state = OwnerToolPolicyState(owner_id="owner 1", policy=ToolPolicy(revision=1)).model_dump(mode="json")
    companion_state = CompanionToolPolicyState(
        companion_id="c/1", lifecycle_state="active", policy=CompanionToolPolicy()
    ).model_dump(mode="json")

    async def data(request):
        seen.append((request.method, request.url.raw_path.decode(), request.headers["Authorization"],
                     json.loads(request.content) if request.content else None))
        return httpx.Response(200, json=owner_state if "/owners/" in request.url.path else companion_state)

    client = _data_client(data)
    change = ToolPolicyChange(expected_revision=1, changes={"get_weather": None})
    assert (await client.owner_tool_policy("owner 1")).policy.revision == 1
    await client.change_owner_tool_policy("owner 1", change)
    assert (await client.companion_tool_policy("c/1")).companion_id == "c/1"
    await client.change_companion_tool_policy("c/1", change)

    body = {"expected_revision": 1, "changes": {"get_weather": None}, "allow_body_control": None}
    assert seen == [
        ("GET", "/api/companion-authority/v1/owners/owner%201/tool-policy", "Bearer data-token", None),
        ("PUT", "/api/companion-authority/v1/owners/owner%201/tool-policy", "Bearer data-token", body),
        ("GET", "/api/companion-authority/v1/companions/c%2F1/tool-policy", "Bearer data-token", None),
        ("PUT", "/api/companion-authority/v1/companions/c%2F1/tool-policy", "Bearer data-token", body),
    ]


@pytest.mark.parametrize(
    ("status", "body", "kind", "public_status", "code"),
    [
        (409, {"detail": {"code": "tool_policy_changed", "message": "the tool policy was changed meanwhile"}},
         "conflict", 409, "tool_policy_changed"),
        (404, {"detail": "active owner not found"}, "not_found", 404, None),
        (422, {"detail": "allow_body_control belongs to a Companion's tool policy"},
         "invalid_request", 422, None),
    ],
)
async def test_the_data_client_maps_datas_refusals(status, body, kind, public_status, code):
    async def data(request):
        return httpx.Response(status, json=body)

    with pytest.raises(AuthorityFailure) as refused:
        await _data_client(data).change_owner_tool_policy(
            "owner-1", ToolPolicyChange(expected_revision=0, changes={"get_weather": False})
        )

    assert (refused.value.kind, refused.value.status_code, refused.value.code) == (kind, public_status, code)


async def test_the_data_client_refuses_another_owners_answer():
    async def data(request):
        return httpx.Response(200, json=OwnerToolPolicyState(
            owner_id="owner-2", policy=ToolPolicy()).model_dump(mode="json"))

    with pytest.raises(AuthorityFailure) as refused:
        await _data_client(data).owner_tool_policy("owner-1")

    assert refused.value.kind == "contract_violation"


async def test_the_agent_client_reads_the_catalog_with_the_agent_credential():
    seen = []

    async def agent(request):
        seen.append((request.url.path, request.headers["Authorization"]))
        return httpx.Response(200, content=CATALOG.model_dump_json())

    async with httpx.AsyncClient(transport=httpx.MockTransport(agent)) as http:
        catalog = await AgentActivityClient(
            base_url="http://agent.test", client=http, timeout_seconds=1, service_token="agent-token",
        ).tool_catalog()
        without = AgentActivityClient(base_url="http://agent.test", client=http, timeout_seconds=1,
                                      service_token="")
        with pytest.raises(AuthorityFailure) as refused:
            await without.tool_catalog()

    assert catalog == CATALOG
    assert seen == [("/api/admin/tools/catalog", "Bearer agent-token")]
    assert (refused.value.kind, refused.value.status_code) == ("configuration", 503)


def _provider_request(provider) -> SimpleNamespace:
    state = SimpleNamespace(
        registry={"channel-provider": SimpleNamespace(
            base_url="http://channel", upstream_prefix="", auth=SimpleNamespace(type="none"))},
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(provider)),
    )
    return SimpleNamespace(app=SimpleNamespace(state=state))


async def test_the_offer_is_read_from_the_provider_and_its_absence_is_none():
    seen = []

    async def provider(request):
        seen.append((request.url.raw_path.decode().split("?")[0], dict(request.url.params)))
        if request.url.path.endswith("/box3/offer"):
            return httpx.Response(200, json=BOX3.model_dump(mode="json"))
        return httpx.Response(404, json={"code": "NOT_FOUND", "detail": "device has no channel on this Host"})

    offers = DeviceOffers(_provider_request(provider))

    assert await offers.offer(owner_id="owner-1", device_id="box3") == BOX3
    assert await offers.offer(owner_id="owner-1", device_id="dev/2") is None
    assert seen == [
        ("/v1/device-channels/box3/offer", {"owner_id": "owner-1"}),
        ("/v1/device-channels/dev%2F2/offer", {"owner_id": "owner-1"}),
    ]


@pytest.mark.parametrize(
    ("answer", "status", "code"),
    [
        (httpx.Response(403, json={"code": "FORBIDDEN", "detail": "device belongs to a different Owner"}),
         403, "FORBIDDEN"),
        (httpx.Response(502, text="bad gateway"), 503, "CHANNEL_PROVIDER_UNAVAILABLE"),
        (httpx.Response(200, json={"operation": "channel.device-offer"}), 503,
         "CHANNEL_PROVIDER_UNAVAILABLE"),
        (httpx.ConnectError("refused"), 503, "CHANNEL_PROVIDER_UNAVAILABLE"),
    ],
)
async def test_an_offer_the_provider_will_not_or_cannot_give_is_refused(answer, status, code):
    async def provider(request):
        if isinstance(answer, Exception):
            raise answer
        return answer

    with pytest.raises(HTTPException) as refused:
        await DeviceOffers(_provider_request(provider)).offer(owner_id="owner-1", device_id="box3")

    assert (refused.value.status_code, refused.value.detail["code"]) == (status, code)


# ----------------------------------------------------------------------------- the routes, end to end


def _internal(data: Data, provider=None) -> FastAPI:
    async def no_channel(request):
        return httpx.Response(404, json={"code": "NOT_FOUND", "detail": "no channel"})

    app = FastAPI()
    install_authority_failure_handler(app)
    app.include_router(tools_router, prefix="/api")
    app.state.settings = SimpleNamespace(local_api_service_token=_SECRET)
    app.state.control_plane = SimpleNamespace(activity=Agent(), data=data)
    app.state.registry = {"channel-provider": SimpleNamespace(
        base_url="http://channel", upstream_prefix="", auth=SimpleNamespace(type="none"))}
    app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(provider or no_channel))
    return app


def _backend(internal: FastAPI) -> AdminManagementClient:
    return AdminManagementClient(
        base_url="http://admin", service_token=_SECRET, timeout_seconds=5,
        client=httpx.AsyncClient(transport=httpx.ASGITransport(app=internal)),
    )


async def test_the_internal_routes_need_the_local_api_credential():
    transport = httpx.ASGITransport(app=_internal(Data()))
    async with httpx.AsyncClient(transport=transport, base_url="http://admin") as client:
        anonymous = await client.get("/api/internal/v1/management/tools", params={"owner_id": "owner-1"})
        answered = await client.get("/api/internal/v1/management/tools", params={"owner_id": "owner-1"},
                                    headers={"Authorization": f"Bearer {_SECRET}"})

    assert anonymous.status_code == 401
    assert answered.status_code == 200
    assert answered.json()["catalog_revision"] == CATALOG.catalog_revision


class _ToolDevices(_Devices):
    """The Owner's devices, and each one's tool restriction as Hub would answer."""

    def __init__(self, *devices, restriction: DeviceToolRestriction | None = None) -> None:
        super().__init__(*devices)
        self.restriction = restriction
        self.restriction_reads: list = []

    async def read_device_tool_restriction(self, *, session, device_ref):
        self.restriction_reads.append((session.owner_id, device_ref))
        return self.restriction


async def _phone(tmp_path, monkeypatch, internal: FastAPI, *devices, restriction=None):
    _stub_controller(monkeypatch)
    devices = _ToolDevices(*(devices or (_device(),)), restriction=restriction)
    transport = httpx.ASGITransport(app=_app(tmp_path, devices, _backend(internal)))
    return httpx.AsyncClient(transport=transport, base_url="https://local.test"), devices


async def test_the_phone_reads_and_switches_tools_for_the_owner_of_its_session(tmp_path, monkeypatch):
    data = Data()
    client, _ = await _phone(tmp_path, monkeypatch, _internal(data))
    async with client:
        assert (await client.get("/api/management/v1/tools")).status_code == 401
        headers = await _authenticate(client)
        read = await client.get("/api/management/v1/tools", headers=headers)
        changed = await client.put(
            "/api/management/v1/tools", headers=headers,
            json={"expected_revision": 0, "changes": {"get_weather": False}},
        )
        stale = await client.put(
            "/api/management/v1/tools", headers=headers,
            json={"expected_revision": 0, "changes": {"get_time": False}},
        )
        unknown = await client.put(
            "/api/management/v1/tools", headers=headers,
            json={"expected_revision": 1, "changes": {"teleport": True}},
        )
        empty = await client.put("/api/management/v1/tools", headers=headers,
                                 json={"expected_revision": 1})

    assert read.status_code == 200
    assert read.json()["tools"][0]["key"] == "audio.volume.set"
    assert changed.status_code == 200
    weather = next(t for t in changed.json()["tools"] if t["key"] == "get_weather")
    assert (changed.json()["revision"], weather["global_enabled"]) == (1, False)
    # The Owner came from the session, never from the request.
    assert data.writes[0][1] == "owner-1"
    # Data's refusal reached the phone with its own code.
    assert stale.status_code == 409
    assert (stale.json()["detail"]["code"], stale.json()["detail"]["kind"]) == (
        "tool_policy_changed", "conflict")
    assert (unknown.status_code, unknown.json()["detail"]["code"]) == (422, "unknown_tool")
    assert empty.status_code == 422
    assert len(data.writes) == 2


async def test_the_phone_reads_and_switches_one_companions_tools(tmp_path, monkeypatch):
    data = Data()
    client, _ = await _phone(tmp_path, monkeypatch, _internal(data))
    async with client:
        headers = await _authenticate(client)
        read = await client.get("/api/management/v1/companions/companion-a/tools", headers=headers)
        changed = await client.put(
            "/api/management/v1/companions/companion-a/tools", headers=headers,
            json={"expected_revision": 0, "changes": {}, "allow_body_control": False},
        )
        reserved = await client.put(
            "/api/management/v1/companions/companion-a/tools", headers=headers,
            json={"expected_revision": 1, "changes": {"delegate_to_coworker": True}},
        )
        elsewhere = await client.get("/api/management/v1/companions/companion-z/tools", headers=headers)

    assert read.status_code == 200
    assert read.json()["allow_body_control"] is True
    assert changed.status_code == 200
    nod = next(t for t in changed.json()["tools"] if t["key"] == "motion.nod")
    assert (changed.json()["allow_body_control"], nod["blocked_by"], nod["companion_enabled"]) == (
        False, "body_control", True)
    assert (reserved.status_code, reserved.json()["detail"]["code"]) == (422, "reserved_tool")
    assert elsewhere.status_code == 404


async def test_the_phone_reads_a_devices_tools_with_its_companion_from_the_inventory(tmp_path, monkeypatch):
    seen = []

    async def provider(request):
        seen.append(dict(request.url.params))
        return httpx.Response(200, json=STACKCHAN_ALL.model_copy(update={"device_id": _KNOWN})
                              .model_dump(mode="json"))

    restriction = DeviceToolRestriction(revision=2, denied=("get_weather", "vision.look"))
    client, devices = await _phone(
        tmp_path, monkeypatch, _internal(Data(), provider), restriction=restriction
    )
    async with client:
        headers = await _authenticate(client)
        answer = await client.get(f"/api/management/v1/devices/{_KNOWN}/tools", headers=headers)
        missing = await client.get("/api/management/v1/devices/not-mine/tools", headers=headers)

    assert answer.status_code == 200
    body = answer.json()
    assert (body["device_id"], body["companion_id"]) == (_KNOWN, "companion-a")
    assert body["restriction"] == {"revision": 2, "denied": ["get_weather", "vision.look"]}
    reasons = {t["key"]: t["reasons"] for t in body["tools"]}
    assert (reasons["stackchan_head"], reasons["delegate_to_coworker"]) == ([], ["reserved"])
    assert (reasons["get_weather"], reasons["vision.look"]) == (["device_restricted"], ["device_restricted"])
    assert seen == [{"owner_id": "owner-1"}]
    # Hub was asked about the DeviceRef the inventory holds, for the session's Owner.
    [(owner, device_ref)] = devices.restriction_reads
    assert (owner, device_ref.device_instance_id) == ("owner-1", _KNOWN)
    # A device that is not in the Owner's inventory is never asked about.
    assert missing.status_code == 404


async def test_a_device_answering_as_nobody_and_without_a_channel(tmp_path, monkeypatch):
    client, _ = await _phone(tmp_path, monkeypatch, _internal(Data()), _device(companion_id=None))
    async with client:
        headers = await _authenticate(client)
        answer = await client.get(f"/api/management/v1/devices/{_KNOWN}/tools", headers=headers)

    body = answer.json()
    assert body["companion_id"] is None
    assert next(t for t in body["tools"] if t["key"] == "music.play")["reasons"] == [
        "no_companion", "channel_not_ready"]
    assert next(t for t in body["tools"] if t["key"] == "get_time")["reasons"] == ["no_companion"]


async def test_the_local_api_client_speaks_the_internal_routes():
    calls = []

    async def admin(request):
        calls.append((request.method, request.url.raw_path.decode().split("?")[0],
                      dict(request.url.params), json.loads(request.content) if request.content else None))
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(admin)) as http:
        client = AdminManagementClient(base_url="http://admin", service_token="t", client=http,
                                       timeout_seconds=5)
        await client.tools(owner_id="owner-1")
        await client.tools(owner_id="owner-1", change={"expected_revision": 0})
        await client.companion_tools(owner_id="owner-1", companion_id="c/1")
        await client.companion_tools(owner_id="owner-1", companion_id="c/1", change={"expected_revision": 2})
        await client.device_tools(owner_id="owner-1", device_id="dev/1", companion_id="c/1",
                                  restriction={"revision": 1, "denied": ["get_weather"]})
        await client.device_tools(owner_id="owner-1", device_id="dev/1", companion_id=None,
                                  restriction=None)

    base = "/api/internal/v1/management"
    assert calls == [
        ("GET", f"{base}/tools", {"owner_id": "owner-1"}, None),
        ("PUT", f"{base}/tools", {"owner_id": "owner-1"}, {"expected_revision": 0}),
        ("GET", f"{base}/companions/c%2F1/tools", {"owner_id": "owner-1"}, None),
        ("PUT", f"{base}/companions/c%2F1/tools", {"owner_id": "owner-1"}, {"expected_revision": 2}),
        ("POST", f"{base}/devices/dev%2F1/tools-queries", {"owner_id": "owner-1"},
         {"companion_id": "c/1", "restriction": {"revision": 1, "denied": ["get_weather"]}}),
        ("POST", f"{base}/devices/dev%2F1/tools-queries", {"owner_id": "owner-1"},
         {"companion_id": None, "restriction": None}),
    ]


# ----------------------------------------------------------------------------- the activity feed


async def test_a_switch_change_reads_in_the_activity_feed_with_only_its_changes():
    feed = await read_activity(
        owner_id="owner-1",
        history=_History(
            _event("owner.tool_policy.changed", subject_type="owner", subject_id="owner-1",
                   payload={"revision": 4, "changes": {"music.play": False, "get_weather": None}}),
            _event("companion.tool_policy.changed", subject_id="companion-b",
                   payload={"revision": 2, "changes": {}, "allow_body_control": False}),
        ),
        companions=_Companions(),
    )

    owner, companion = feed.moments
    assert owner.detail == {"changes": '{"get_weather": null, "music.play": false}'}
    assert json.loads(owner.detail["changes"]) == {"music.play": False, "get_weather": None}
    assert companion.subject_name == "阿力"
    assert companion.detail == {"changes": "{}", "allow_body_control": "false"}
