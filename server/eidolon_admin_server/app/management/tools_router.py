"""Internal routes for the Owner's tool switches; the Local API is the only caller.

``owner_id`` arrives as a query parameter, as on every internal management
route: Local API passes the Owner bound to the Controller session it verified.
For a device it also passes the Companion that device answers as, read from the
same inventory that proved the device is this Owner's.
"""

from __future__ import annotations

from eidolon_sdk.biz.tools import ToolPolicyChange
from fastapi import APIRouter, Depends, Request

from eidolon_admin_server.app.service_auth import require_local_api_credential

from .device_settings_router import DeviceOffers
from .tools import CompanionTools, DeviceTools, ToolGovernance, ToolsOverview

router = APIRouter(
    prefix="/internal/v1/management",
    tags=["management-internal"],
    dependencies=[Depends(require_local_api_credential)],
)


def _governance(request: Request) -> ToolGovernance:
    control_plane = request.app.state.control_plane
    return ToolGovernance(
        catalog=control_plane.activity,
        policies=control_plane.data,
        roster=control_plane.data,
        offers=DeviceOffers(request),
    )


@router.get("/tools", response_model=ToolsOverview)
async def overview(request: Request, owner_id: str):
    return await _governance(request).overview(owner_id)


@router.put("/tools", response_model=ToolsOverview)
async def change_overview(request: Request, owner_id: str, body: ToolPolicyChange):
    return await _governance(request).change_overview(owner_id, body)


@router.get("/companions/{companion_id}/tools", response_model=CompanionTools)
async def companion_tools(request: Request, companion_id: str, owner_id: str):
    return await _governance(request).companion(owner_id, companion_id)


@router.put("/companions/{companion_id}/tools", response_model=CompanionTools)
async def change_companion_tools(
    request: Request, companion_id: str, owner_id: str, body: ToolPolicyChange
):
    return await _governance(request).change_companion(owner_id, companion_id, body)


@router.get("/devices/{device_id}/tools", response_model=DeviceTools)
async def device_tools(
    request: Request, device_id: str, owner_id: str, companion_id: str | None = None
):
    return await _governance(request).device(owner_id, device_id, companion_id)
