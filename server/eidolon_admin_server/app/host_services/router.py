"""REST surface for Host service control.

Routes (mounted at /api/host):
    GET  /services                       every service eidolond manages
    GET  /services/{service_id}          one service
    POST /services/{service_id}/restart  restart, carrying the observed revision
    POST /services/{service_id}/enable
    POST /services/{service_id}/disable

This is the same surface on both Hosts: eidolond drives supervisord on macOS
and systemd on the Pi. The older /api/supervisor routes only speak supervisord
and therefore only work on a development Mac.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from eidolon_sdk.system.v1 import HostVitalsWire

from .contracts import (
    HostService,
    HostServiceMutationResult,
    HostServicePage,
)
from .errors import HostServiceError

router = APIRouter(prefix="/host", tags=["host-services"])


class MutationRequest(BaseModel):
    """Compare-and-swap intent.

    The revision the operator saw is required, so two operators acting on the
    same stale view cannot both win.
    """

    expected_revision: int = Field(ge=1)
    request_id: str | None = Field(default=None, min_length=1, max_length=96)


def _client(request: Request):
    client = getattr(request.app.state, "host_services", None)
    if client is None:
        raise HTTPException(
            status_code=503,
            detail="Host service control is not configured on this Admin instance",
        )
    return client


def _fail(error: HostServiceError) -> HTTPException:
    return HTTPException(status_code=error.status_code, detail=error.detail)


@router.get("/capabilities")
async def list_capabilities(request: Request) -> dict[str, object]:
    """What this Host can actually offer.

    The Web UI uses this to avoid showing a page that cannot work here; a
    product Host has no firmware or Android tooling.
    """

    capabilities = getattr(request.app.state, "workstation_capabilities", ())
    return {"workstation": [item.to_wire() for item in capabilities]}


@router.get("/vitals", response_model=HostVitalsWire)
async def host_vitals(request: Request) -> HostVitalsWire:
    try:
        return await _client(request).read_vitals()
    except HostServiceError as exc:
        raise _fail(exc) from exc


@router.get("/services", response_model=HostServicePage)
async def list_services(request: Request) -> HostServicePage:
    try:
        return await _client(request).list_services()
    except HostServiceError as exc:
        raise _fail(exc) from exc


@router.get("/services/{service_id}", response_model=HostService)
async def get_service(service_id: str, request: Request) -> HostService:
    try:
        return await _client(request).get_service(service_id)
    except HostServiceError as exc:
        raise _fail(exc) from exc


async def _mutate(
    service_id: str,
    operation: str,
    payload: MutationRequest,
    request: Request,
) -> HostServiceMutationResult:
    try:
        return await _client(request).mutate(
            service_id=service_id,
            operation=operation,
            expected_revision=payload.expected_revision,
            request_id=payload.request_id,
        )
    except HostServiceError as exc:
        raise _fail(exc) from exc


@router.post("/services/{service_id}/restart", response_model=HostServiceMutationResult)
async def restart_service(
    service_id: str, payload: MutationRequest, request: Request
) -> HostServiceMutationResult:
    return await _mutate(service_id, "restart", payload, request)


@router.post("/services/{service_id}/enable", response_model=HostServiceMutationResult)
async def enable_service(
    service_id: str, payload: MutationRequest, request: Request
) -> HostServiceMutationResult:
    return await _mutate(service_id, "enable", payload, request)


@router.post("/services/{service_id}/disable", response_model=HostServiceMutationResult)
async def disable_service(
    service_id: str, payload: MutationRequest, request: Request
) -> HostServiceMutationResult:
    return await _mutate(service_id, "disable", payload, request)
