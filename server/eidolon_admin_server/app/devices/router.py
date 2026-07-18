"""Device fleet endpoint: one server-side join of hub presence/approval +
eidolon_data ownership, grouped by owner → companion. Lets the Device Center
render ownership in a single call instead of reconciling two sources client-side.
Reuses the mission_control device-merge so the join stays in one place."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from ..mission_control.schemas import RuntimeDevice, SourceStatus
from ..mission_control.service import (
    _active_guard_bindings,
    _hub_devices,
    _merge_devices,
    _runtime_blackboard,
    _select_owner,
    _store,
)

router = APIRouter(prefix="/devices", tags=["devices"])


class FleetGroup(BaseModel):
    companion_id: str
    companion_name: str
    devices: list[RuntimeDevice] = Field(default_factory=list)


class FleetResponse(BaseModel):
    owner_id: str = ""
    groups: list[FleetGroup] = Field(default_factory=list)
    unbound: list[RuntimeDevice] = Field(default_factory=list)


@router.get("/fleet", response_model=FleetResponse)
async def fleet(
    request: Request,
    owner_id: str | None = Query(default=None),
) -> FleetResponse:
    store = _store(request)
    if store is None:
        return FleetResponse()
    owner = await _select_owner(store, owner_id)
    if owner is None:
        return FleetResponse(owner_id=owner_id or "")

    statuses: list[SourceStatus] = []
    try:
        companions = await store.companions.list_for_owner(owner.owner_id)
    except Exception:  # noqa: BLE001 - degrade gracefully
        companions = []
    try:
        data_devices = await store.devices.list_devices_for_owner(owner.owner_id)
    except Exception:  # noqa: BLE001
        data_devices = []
    try:
        guard_bindings = await store.guard_bindings.list_for_owner(owner.owner_id)
    except Exception:  # noqa: BLE001
        guard_bindings = []
    # device_id -> guard companion_id for live guard bindings. This binding, not
    # the device's hardware kind, is what makes a device a guard sentinel.
    guard_companion_by_device = _active_guard_bindings(guard_bindings)
    hub_devices = await _hub_devices(request, statuses)
    runtime_blackboard = await _runtime_blackboard(request, owner.owner_id, statuses)
    merged = _merge_devices(
        data_devices,
        hub_devices,
        runtime_blackboard=runtime_blackboard,
        owner_id=owner.owner_id,
        companions=companions,
        guard_device_ids=frozenset(guard_companion_by_device),
    )

    by_companion: dict[str, list[RuntimeDevice]] = {}
    unbound: list[RuntimeDevice] = []
    known = {c.companion_id for c in companions}
    for device in merged:
        # Guard devices bind to their guard companion via guard_bindings, not
        # via bound_companion_id — group them under that companion.
        cid = guard_companion_by_device.get(device.device_id) or device.companion_id
        if cid and cid in known:
            by_companion.setdefault(cid, []).append(device)
        else:
            unbound.append(device)

    groups = [
        FleetGroup(
            companion_id=c.companion_id,
            companion_name=c.display_name or c.companion_id,
            devices=by_companion.get(c.companion_id, []),
        )
        for c in companions
    ]
    return FleetResponse(owner_id=owner.owner_id, groups=groups, unbound=unbound)
