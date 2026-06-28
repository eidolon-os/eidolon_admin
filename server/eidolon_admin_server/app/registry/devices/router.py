"""FastAPI router for ``/api/devices/*`` — new model.

Replaces the Phase 25 surface (which had device-creates-agent semantics
under ``/api/devices/{id}/agents*``). The new routes treat device and
agent as independent first-class entities, with the device just
pointing at a chosen agent via ``bind``.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..schemas.device import (
    BindDeviceRequest,
    DeviceListResponse,
    DeviceView,
)
from .orchestrator import DeviceError, DeviceOrchestrator

router = APIRouter(prefix="/devices", tags=["devices"])


def _orchestrator(request: Request) -> DeviceOrchestrator:
    orch: DeviceOrchestrator | None = getattr(
        request.app.state, "device_orchestrator", None
    )
    if orch is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "device orchestrator unavailable — admin booted without "
                "the hub service URL or registry DB"
            ),
        )
    return orch


@router.get("", response_model=DeviceListResponse)
async def list_devices(request: Request) -> DeviceListResponse:
    orch = _orchestrator(request)
    try:
        devices = await orch.list_devices()
        discovery = await orch.get_discovery_status()
        return DeviceListResponse(
            devices=devices,
            hub_available=True,
            discovery=discovery,
        )
    except DeviceError as exc:
        if exc.status_code == 503:
            return DeviceListResponse(
                devices=[],
                hub_available=False,
                discovery=None,
            )
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("/{device_id}", response_model=DeviceView)
async def get_device(device_id: str, request: Request) -> DeviceView:
    orch = _orchestrator(request)
    try:
        return await orch.get_device(device_id)
    except DeviceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/{device_id}/approve", response_model=DeviceView)
async def approve_device(device_id: str, request: Request) -> DeviceView:
    orch = _orchestrator(request)
    try:
        return await orch.approve_device(device_id)
    except DeviceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/{device_id}/enable", response_model=DeviceView)
async def set_device_enabled(
    device_id: str, enabled: bool, request: Request
) -> DeviceView:
    orch = _orchestrator(request)
    try:
        return await orch.set_device_enabled(device_id, enabled=enabled)
    except DeviceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/{device_id}/bind", response_model=DeviceView)
async def bind_device(
    device_id: str, body: BindDeviceRequest, request: Request
) -> DeviceView:
    orch = _orchestrator(request)
    try:
        return await orch.bind_device(device_id, body)
    except DeviceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/{device_id}/unbind", response_model=DeviceView)
async def unbind_device(device_id: str, request: Request) -> DeviceView:
    """Idempotent — clears the binding. Device stays in hub (still
    approved); just no longer configured to talk."""
    orch = _orchestrator(request)
    try:
        return await orch.unbind_device(device_id)
    except DeviceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/{device_id}/wake", status_code=200)
async def wake_device(device_id: str, request: Request) -> dict:
    """Send a control command asking the device to join its voice room."""
    orch = _orchestrator(request)
    try:
        return await orch.wake_device(device_id)
    except DeviceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/{device_id}/identify", status_code=200)
async def identify_device(device_id: str, request: Request) -> dict:
    """Ask an online/reachable device to identify itself."""
    orch = _orchestrator(request)
    try:
        return await orch.identify_device(device_id)
    except DeviceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/{device_id}/refresh-config", status_code=200)
async def refresh_device_config(device_id: str, request: Request) -> dict:
    """Ask a connected device to pull fresh Hub/Admin runtime config."""
    orch = _orchestrator(request)
    try:
        await orch.refresh_device_config(device_id)
        return {"device_id": device_id, "status": "sent"}
    except DeviceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.delete("/{device_id}", status_code=200)
async def unregister_device(device_id: str, request: Request) -> dict:
    """Cascade: drop admin's binding + tell hub to forget the device.
    Returns hub's envelope (existed + presence_cleared flags)."""
    orch = _orchestrator(request)
    try:
        return await orch.unregister_device(device_id)
    except DeviceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
