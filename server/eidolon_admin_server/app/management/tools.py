"""The Owner's tool switches as a management client reads and changes them.

Admin only composes. Which tools exist, and what each needs, is the Agent's
catalog; both layers of switches are Data's; what a device's channel offers a
session is the Channel Provider's. Whether a switch grants a tool is decided by
the SDK's one rule, ``blocked_by``, and never restated here.

A change is checked against the catalog before it reaches Data: a switch for a
tool this Host does not have, or for one the system reserves, is refused here
rather than stored as an intent nobody can act on. A stale revision is Data's
409 ``tool_policy_changed``, relayed as it arrived. Every refusal travels as an
``AuthorityFailure`` to the one handler that turns refusals into responses.
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from eidolon_sdk.biz.tools import (
    BlockedBy,
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
    blocked_by,
)
from pydantic import BaseModel, ConfigDict

from eidolon_admin_server.app.control_plane.errors import AuthorityFailure
from eidolon_admin_server.app.management.roster import RosterReader

logger = logging.getLogger(__name__)

#: The kinds whose use depends on what a device's channel offers a session.
DEVICE_KINDS = frozenset({"channel", "device", "agent_device"})

_LAYER_REASONS: dict[BlockedBy, str] = {
    "global": "global_off",
    "body_control": "body_control_off",
    "companion": "companion_off",
}


@runtime_checkable
class ToolCatalogReader(Protocol):
    """The Agent's catalog: every tool the Owner's switches cover."""

    async def tool_catalog(self) -> ToolCatalog: ...


@runtime_checkable
class ToolPolicyStore(Protocol):
    """Both layers of switches, as Data keeps them."""

    async def owner_tool_policy(self, owner_id: str) -> OwnerToolPolicyState: ...

    async def change_owner_tool_policy(
        self, owner_id: str, change: ToolPolicyChange
    ) -> OwnerToolPolicyState: ...

    async def companion_tool_policy(self, companion_id: str) -> CompanionToolPolicyState: ...

    async def change_companion_tool_policy(
        self, companion_id: str, change: ToolPolicyChange
    ) -> CompanionToolPolicyState: ...


class DeviceOfferReader(Protocol):
    """What a device's channel offers a session; ``None`` when it has no channel yet."""

    async def offer(self, *, owner_id: str, device_id: str) -> DeviceToolOffer | None: ...


# ----------------------------------------------------------------------------- views


class _View(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CompanionSwitch(_View):
    """One Companion's own switch for a tool, beside what it amounts to."""

    companion_id: str
    name: str
    #: The Companion's own value, kept when the Owner's switch is off.
    enabled: bool
    effective: bool


class OverviewTool(_View):
    key: str
    label: str
    summary: str
    group: str
    kind: str
    side_effect: bool
    reserved: ToolReservation | None
    default_enabled: bool
    global_enabled: bool
    global_overridden: bool
    requires: ToolRequirements
    host_available: bool | None
    companions: list[CompanionSwitch]


class ToolsOverview(_View):
    revision: int
    catalog_revision: str
    tools: list[OverviewTool]


class CompanionTool(_View):
    key: str
    label: str
    summary: str
    group: str
    kind: str
    reserved: ToolReservation | None
    default_enabled: bool
    global_enabled: bool
    companion_enabled: bool
    companion_overridden: bool
    effective: bool
    body_control: bool
    blocked_by: BlockedBy | None
    requires: ToolRequirements


class CompanionTools(_View):
    companion_id: str
    revision: int
    allow_body_control: bool
    tools: list[CompanionTool]


class DeviceTool(_View):
    key: str
    label: str
    summary: str
    group: str
    kind: str
    available: bool
    reasons: list[str]
    requires: ToolRequirements


class DeviceTools(_View):
    device_id: str
    companion_id: str | None
    tools: list[DeviceTool]
    restriction: DeviceToolRestriction | None


# ----------------------------------------------------------------------------- rules


def _ordered(catalog: ToolCatalog) -> list[ToolCatalogEntry]:
    """By group, then key, reserved tools last: an order every screen can rely on."""

    return sorted(catalog.tools, key=lambda entry: (entry.reserved is not None, entry.group, entry.key))


def _switch(policy: ToolPolicy, entry: ToolCatalogEntry) -> bool:
    return policy.overrides.get(entry.key, entry.default_enabled)


def device_reasons(
    entry: ToolCatalogEntry,
    *,
    owner: ToolPolicy,
    companion: CompanionToolPolicy | None,
    offer: DeviceToolOffer | None,
    restriction: DeviceToolRestriction | None,
) -> list[str]:
    """Why ``entry`` cannot be used on this device, in order; empty when it can.

    ``companion`` is the policy of the Companion bound to the device, ``None``
    when none is. ``offer`` is what the device's channel offers a session,
    ``None`` when it has no channel yet. ``restriction`` is what the Owner
    withheld on this device, ``None`` when nothing ever was. The switches go
    through the SDK's one rule; the device facts are the Provider's, only
    translated into codes.
    """

    if entry.reserved is not None:
        return ["reserved"]
    reasons: list[str] = []
    layer = blocked_by(entry, owner, companion)
    if layer == "global":
        reasons.append("global_off")
    if companion is None:
        reasons.append("no_companion")
    elif layer is not None and layer != "global":
        reasons.append(_LAYER_REASONS[layer])
    restricted = restriction is not None and entry.key in restriction.denied
    if restricted:
        reasons.append("device_restricted")

    if entry.kind in DEVICE_KINDS:
        reasons.extend(_device_facts(entry, offer, restricted=restricted))

    if entry.host_available is False:
        feature = entry.requires.host_feature
        reasons.append(f"host_not_configured:{feature}" if feature else "host_not_configured")
    return reasons


def _device_facts(
    entry: ToolCatalogEntry, offer: DeviceToolOffer | None, *, restricted: bool
) -> list[str]:
    if offer is None:
        return ["channel_not_ready"]
    requires = entry.requires
    facts: list[str] = []

    def add(code: str) -> None:
        if code not in facts:
            facts.append(code)

    if requires.motion_profile is not None and offer.declared_motion_profile != requires.motion_profile:
        add("motion_profile_mismatch")
    for contract in requires.device_contracts:
        if contract not in offer.declared_capabilities:
            add(f"device_not_declared:{contract}")
    for output in requires.outputs:
        if not (offer.outputs or {}).get(output):
            add(f"output_not_allowed:{output}")
    if requires.inputs.get("camera") == "capture" and "camera.capture" not in offer.session_capabilities:
        add("input_not_allowed:camera" if "camera.capture" in offer.declared_capabilities
            else "device_not_declared:camera.capture")
    if entry.kind == "device" and entry.key not in offer.session_capabilities:
        if entry.key not in offer.declared_capabilities:
            add(f"device_not_declared:{entry.key}")
        elif not facts and not restricted:
            # The checks above restate the contract's own requirements, so a
            # declared contract the session still lacks means they disagree
            # with the Provider. Logged, not invented as a reason. A restricted
            # one is expected: the grant leaves restricted contracts out.
            logger.warning(
                "device %s declares %s and meets its requirements, yet its channel does not offer it",
                offer.device_id, entry.key,
            )
    return facts


def _check(catalog: ToolCatalog, change: ToolPolicyChange) -> None:
    """Refuse a switch for a tool this Host does not have, or one the system reserves."""

    unknown = sorted(key for key in change.changes if catalog.entry(key) is None)
    if unknown:
        raise AuthorityFailure(
            "agent", "invalid_request", f"这台主机没有这些工具：{'、'.join(unknown)}", 422,
            code="unknown_tool",
        )
    reserved = sorted(key for key in change.changes if catalog.entry(key).reserved is not None)
    if reserved:
        raise AuthorityFailure(
            "agent", "invalid_request", f"系统保留的工具不能更改：{'、'.join(reserved)}", 422,
            code="reserved_tool",
        )


def _overview(catalog: ToolCatalog, state: OwnerToolPolicyState, names: dict[str, str]) -> ToolsOverview:
    companions = [c for c in state.companions if c.companion_id in names]
    return ToolsOverview(
        revision=state.policy.revision,
        catalog_revision=catalog.catalog_revision,
        tools=[
            OverviewTool(
                key=entry.key, label=entry.label, summary=entry.summary, group=entry.group,
                kind=entry.kind, side_effect=entry.side_effect, reserved=entry.reserved,
                default_enabled=entry.default_enabled,
                global_enabled=_switch(state.policy, entry),
                global_overridden=entry.key in state.policy.overrides,
                requires=entry.requires, host_available=entry.host_available,
                companions=[
                    CompanionSwitch(
                        companion_id=c.companion_id, name=names[c.companion_id],
                        enabled=_switch(c.policy, entry),
                        effective=blocked_by(entry, state.policy, c.policy) is None,
                    )
                    for c in companions
                ],
            )
            for entry in _ordered(catalog)
        ],
    )


def _companion_tools(
    catalog: ToolCatalog, owner: ToolPolicy, state: CompanionToolPolicyState
) -> CompanionTools:
    policy = state.policy
    tools = []
    for entry in _ordered(catalog):
        layer = blocked_by(entry, owner, policy)
        tools.append(CompanionTool(
            key=entry.key, label=entry.label, summary=entry.summary, group=entry.group,
            kind=entry.kind, reserved=entry.reserved, default_enabled=entry.default_enabled,
            global_enabled=_switch(owner, entry),
            companion_enabled=_switch(policy, entry),
            companion_overridden=entry.key in policy.overrides,
            effective=layer is None, body_control=entry.body_control, blocked_by=layer,
            requires=entry.requires,
        ))
    return CompanionTools(
        companion_id=state.companion_id, revision=policy.revision,
        allow_body_control=policy.allow_body_control, tools=tools,
    )


# ----------------------------------------------------------------------------- service


class ToolGovernance:
    """The Owner-facing operations.

    ``catalog`` is the Agent, ``policies`` and ``roster`` are Data (the roster
    proves a Companion is this Owner's and names it), ``offers`` the Channel
    Provider. The Owner always arrives as an argument, never chosen here.
    """

    def __init__(
        self,
        *,
        catalog: ToolCatalogReader,
        policies: ToolPolicyStore,
        roster: RosterReader,
        offers: DeviceOfferReader,
    ) -> None:
        self._catalog, self._policies, self._roster, self._offers = catalog, policies, roster, offers

    async def _names(self, owner_id: str) -> dict[str, str]:
        """What each of this Owner's Companions is called, every roster page of it."""

        names: dict[str, str] = {}
        cursor = None
        while True:
            page = await self._roster.list_owner_companions(owner_id, cursor=cursor)
            names.update((row.companion_id, row.display_name) for row in page.companions)
            if page.next_cursor is None:
                return names
            cursor = page.next_cursor

    # ------------------------------------------------------------------ the Owner's switches

    async def overview(self, owner_id: str) -> ToolsOverview:
        catalog = await self._catalog.tool_catalog()
        state = await self._policies.owner_tool_policy(owner_id)
        return _overview(catalog, state, await self._names(owner_id))

    async def change_overview(self, owner_id: str, change: ToolPolicyChange) -> ToolsOverview:
        if change.allow_body_control is not None:
            raise AuthorityFailure(
                "data", "invalid_request", "「允许操控身体」只能在伙伴上设置", 422,
            )
        catalog = await self._catalog.tool_catalog()
        _check(catalog, change)
        state = await self._policies.change_owner_tool_policy(owner_id, change)
        return _overview(catalog, state, await self._names(owner_id))

    # ------------------------------------------------------------------ a Companion's switches

    async def companion(self, owner_id: str, companion_id: str) -> CompanionTools:
        await self._roster.get_owner_companion(owner_id, companion_id)
        catalog = await self._catalog.tool_catalog()
        owner = await self._policies.owner_tool_policy(owner_id)
        state = await self._policies.companion_tool_policy(companion_id)
        return _companion_tools(catalog, owner.policy, state)

    async def change_companion(
        self, owner_id: str, companion_id: str, change: ToolPolicyChange
    ) -> CompanionTools:
        # Checked before anything reaches Data, ownership included: a change
        # that cannot be stored is refused whoever's Companion it names.
        catalog = await self._catalog.tool_catalog()
        _check(catalog, change)
        await self._roster.get_owner_companion(owner_id, companion_id)
        state = await self._policies.change_companion_tool_policy(companion_id, change)
        owner = await self._policies.owner_tool_policy(owner_id)
        return _companion_tools(catalog, owner.policy, state)

    # ------------------------------------------------------------------ one device

    async def device(
        self,
        owner_id: str,
        device_id: str,
        companion_id: str | None,
        restriction: DeviceToolRestriction | None,
    ) -> DeviceTools:
        """Which tools a session on this device could use now, and why not the others.

        ``device_id``, ``companion_id`` and ``restriction`` come from Local API,
        which read them under the Owner's Controller (the inventory and Hub),
        never from a client. The Companion is checked against the Owner's own
        switches all the same: one the Owner does not keep is a disagreement
        between authorities, not a device with defaults.
        """

        catalog = await self._catalog.tool_catalog()
        state = await self._policies.owner_tool_policy(owner_id)
        companion = None
        if companion_id is not None:
            held = next((c for c in state.companions if c.companion_id == companion_id), None)
            if held is None:
                raise AuthorityFailure(
                    "data", "conflict", "这台设备的伙伴不在你的伙伴里，请稍后再看", 409,
                )
            companion = held.policy
        offer = await self._offers.offer(owner_id=owner_id, device_id=device_id)
        tools = []
        for entry in _ordered(catalog):
            reasons = device_reasons(
                entry, owner=state.policy, companion=companion, offer=offer,
                restriction=restriction,
            )
            tools.append(DeviceTool(
                key=entry.key, label=entry.label, summary=entry.summary, group=entry.group,
                kind=entry.kind, available=not reasons, reasons=reasons, requires=entry.requires,
            ))
        return DeviceTools(
            device_id=device_id, companion_id=companion_id, tools=tools, restriction=restriction
        )
