"""Tools on the phone: the Owner's switches, each Companion's, and what one device can use.

The tools' names and sentences come from the Host's catalog, so a tool a
package added yesterday is named without an App release. Group names and the
wording of every reason are the client's: both are short, fixed vocabularies.

A change carries only what changed and the revision it was made against, so one
switch never overwrites another just changed; a stale revision is a 409 the
client answers by reading again.
"""

from __future__ import annotations

from eidolon_sdk.biz.tools import (
    BlockedBy,
    DeviceToolRestriction,
    ToolPolicyChange,
    ToolRequirements,
    ToolReservation,
)
from fastapi import Header
from pydantic import BaseModel, ConfigDict, Field

from .backend import ManagementBackendError
from .router import refusal_for_status


class _View(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ToolCompanionSwitchView(_View):
    companion_id: str
    name: str
    #: The revision of this Companion's switches: what a change to them names.
    revision: int = Field(ge=0)
    #: The Companion's own switch. Kept while the Owner's switch is off, so
    #: turning that back on restores each Companion's choice.
    enabled: bool
    #: Whether this Companion may use the tool now, all switches considered.
    effective: bool
    #: Which switch keeps it from this Companion: reserved, global,
    #: body_control or companion; null when none does.
    blocked_by: BlockedBy | None


class ToolOverviewRowView(_View):
    key: str
    label: str
    summary: str
    group: str
    #: ``brain``, ``channel``, ``device`` or ``agent_device``.
    kind: str
    side_effect: bool
    #: Set when the system keeps this tool out of conversations; it is shown
    #: locked and cannot be switched.
    reserved: ToolReservation | None
    default_enabled: bool
    global_enabled: bool
    #: Whether the Owner's switch differs from the default ("恢复默认" writes null).
    global_overridden: bool
    requires: ToolRequirements
    #: Whether this Host has what the tool needs; null when the Host cannot tell.
    host_available: bool | None
    companions: list[ToolCompanionSwitchView]


class ToolsOverviewView(_View):
    """Every tool this Host has, with the Owner's switch and each Companion's."""

    #: The Owner's switches' revision; send it back as ``expected_revision``.
    revision: int
    catalog_revision: str
    tools: list[ToolOverviewRowView]


class CompanionToolRowView(_View):
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
    #: Governed by the Companion's 「允许操控身体」 as well as its own switch.
    body_control: bool
    #: The first layer that keeps this tool from the Companion, null when none does.
    blocked_by: BlockedBy | None
    requires: ToolRequirements


class CompanionToolsView(_View):
    """What one Companion may use, and its own switches beneath the Owner's."""

    companion_id: str
    #: This Companion's switches' revision; send it back as ``expected_revision``.
    revision: int
    #: The master switch over every tool that moves or tunes its body.
    allow_body_control: bool
    tools: list[CompanionToolRowView]


class DeviceToolRowView(_View):
    key: str
    label: str
    summary: str
    group: str
    kind: str
    available: bool
    #: Every reason it cannot be used here, in order; empty when it can. Codes:
    #: ``reserved``, ``global_off``, ``no_companion``, ``body_control_off``,
    #: ``companion_off``, ``device_restricted``, ``channel_not_ready``,
    #: ``motion_profile_mismatch``, ``device_not_declared:<contract>``,
    #: ``output_not_allowed:<output>``, ``input_not_allowed:camera``,
    #: ``host_not_configured:<feature>``.
    reasons: list[str]
    requires: ToolRequirements


class DeviceToolsView(_View):
    """Which tools a conversation on this device could use now, and why not the others."""

    device_id: str
    #: The Companion this device answers as; null when it answers as none.
    companion_id: str | None
    tools: list[DeviceToolRowView]
    #: What its Owner withheld on this device; null when nothing ever was. Send
    #: its revision back as ``expected_revision`` when changing it.
    restriction: DeviceToolRestriction | None


def register_tool_routes(
    router, backend, authenticated_owner, authenticated_controller_session, devices, refused
):
    @router.get("/tools", response_model=ToolsOverviewView)
    async def get_tools(authorization: str | None = Header(default=None)):
        owner = await authenticated_owner(authorization)
        try:
            return ToolsOverviewView.model_validate(await backend.tools(owner_id=owner))
        except ManagementBackendError as exc:
            raise refused(exc) from exc

    @router.put("/tools", response_model=ToolsOverviewView)
    async def put_tools(body: ToolPolicyChange, authorization: str | None = Header(default=None)):
        owner = await authenticated_owner(authorization)
        try:
            answer = await backend.tools(owner_id=owner, change=body.model_dump(mode="json"))
        except ManagementBackendError as exc:
            raise refused(exc) from exc
        return ToolsOverviewView.model_validate(answer)

    @router.get("/companions/{companion_id}/tools", response_model=CompanionToolsView)
    async def get_companion_tools(companion_id: str, authorization: str | None = Header(default=None)):
        owner = await authenticated_owner(authorization)
        try:
            answer = await backend.companion_tools(owner_id=owner, companion_id=companion_id)
        except ManagementBackendError as exc:
            raise refused(exc) from exc
        return CompanionToolsView.model_validate(answer)

    @router.put("/companions/{companion_id}/tools", response_model=CompanionToolsView)
    async def put_companion_tools(
        companion_id: str, body: ToolPolicyChange, authorization: str | None = Header(default=None)
    ):
        owner = await authenticated_owner(authorization)
        try:
            answer = await backend.companion_tools(
                owner_id=owner, companion_id=companion_id, change=body.model_dump(mode="json")
            )
        except ManagementBackendError as exc:
            raise refused(exc) from exc
        return CompanionToolsView.model_validate(answer)

    @router.get("/devices/{device_id}/tools", response_model=DeviceToolsView)
    async def get_device_tools(device_id: str, authorization: str | None = Header(default=None)):
        """Which tools my Eidolon could use through this device of mine.

        Which device is proved from the inventory this Owner holds; the
        Companion it answers as is read from the same row, and its restriction
        from Hub for the DeviceRef in that row, so none of it comes from the
        phone.
        """
        session = await authenticated_controller_session(authorization)
        try:
            inventory = await devices.list_devices(session=session)
            held = next((d for d in inventory.devices if d.device_id == device_id), None)
            if held is None:
                raise ManagementBackendError(
                    "Device is not mounted", status_code=404,
                    refusal=refusal_for_status(404, "Device is not mounted"),
                )
            restriction = await devices.read_device_tool_restriction(
                session=session, device_ref=held.claim.device_ref
            )
            answer = await backend.device_tools(
                owner_id=session.owner_id, device_id=device_id,
                companion_id=held.body.answering_companion_id,
                restriction=None if restriction is None else restriction.model_dump(mode="json"),
            )
        except ManagementBackendError as exc:
            raise refused(exc) from exc
        return DeviceToolsView.model_validate(answer)

