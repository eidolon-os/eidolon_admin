"""HTTP interface for Admin-owned control-plane orchestration."""

from __future__ import annotations

import hmac
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request, Response
from eidolon_sdk.biz.system_data import CompanionRuntimeSnapshot

from .contracts import (
    BoundaryCapabilities,
    CompanionIdentity,
    DeviceAdmissionRequest,
    DeviceAdmissionResult,
    KernelMountPage,
    OwnerInventory,
    WorkspaceInitializeRequest,
    WorkspaceOperation,
)
from .errors import AuthorityFailure
from .service import ControlPlaneService

router = APIRouter(prefix="/control-plane/v1", tags=["control-plane"])


def _service(request: Request) -> ControlPlaneService:
    return request.app.state.control_plane


def _raise(exc: AuthorityFailure) -> None:
    raise HTTPException(
        status_code=exc.status_code, detail=exc.to_wire().model_dump()
    ) from exc


def _authorize_local_api(request: Request, authorization: str | None) -> None:
    expected = request.app.state.settings.local_api_service_token.strip()
    if not expected:
        raise HTTPException(503, "Local API service credential is not configured")
    scheme, separator, token = (authorization or "").partition(" ")
    if (
        separator != " "
        or scheme.lower() != "bearer"
        or not hmac.compare_digest(token, expected)
    ):
        raise HTTPException(401, "Local API service authentication failed")


@router.get("/capabilities", response_model=BoundaryCapabilities)
async def capabilities(request: Request) -> BoundaryCapabilities:
    return _service(request).capabilities()


@router.get("/companions/{companion_id}", response_model=CompanionIdentity)
async def get_companion(companion_id: str, request: Request) -> CompanionIdentity:
    try:
        return await _service(request).data.get_companion(companion_id)
    except AuthorityFailure as exc:
        _raise(exc)


@router.put(
    "/workspace-onboarding/operations/{operation_id}",
    response_model=WorkspaceOperation,
)
async def initialize_workspace(
    operation_id: UUID,
    payload: WorkspaceInitializeRequest,
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> WorkspaceOperation:
    _authorize_local_api(request, authorization)
    try:
        return await _service(request).initialize_workspace(
            operation_id=str(operation_id),
            payload=payload,
        )
    except AuthorityFailure as exc:
        _raise(exc)


@router.get(
    "/workspace-onboarding/operations/{operation_id}",
    response_model=WorkspaceOperation,
)
async def get_workspace_operation(
    operation_id: UUID,
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> WorkspaceOperation:
    _authorize_local_api(request, authorization)
    try:
        return await _service(request).get_workspace_operation(str(operation_id))
    except AuthorityFailure as exc:
        _raise(exc)


@router.get(
    "/owners/{owner_id}/primary-runtime-snapshot",
    response_model=CompanionRuntimeSnapshot,
)
async def get_owner_primary_runtime(
    owner_id: str,
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> CompanionRuntimeSnapshot:
    _authorize_local_api(request, authorization)
    try:
        return await _service(request).get_owner_primary_runtime(owner_id)
    except AuthorityFailure as exc:
        _raise(exc)


@router.get(
    "/owners/{owner_id}/device-mounts",
    response_model=KernelMountPage,
)
async def get_owner_device_mounts(
    owner_id: str,
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> KernelMountPage:
    """Narrow product projection used only by the loopback Local API."""

    _authorize_local_api(request, authorization)
    try:
        return await _service(request).list_owner_device_mounts(owner_id)
    except AuthorityFailure as exc:
        _raise(exc)


@router.get("/owners/{owner_id}/inventory", response_model=OwnerInventory)
async def owner_inventory(
    owner_id: str,
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> OwnerInventory:
    try:
        return await _service(request).inventory(
            owner_id=owner_id,
            hub_authorization=authorization or "",
        )
    except AuthorityFailure as exc:
        _raise(exc)


@router.post("/workflows/device-admission", response_model=DeviceAdmissionResult)
async def admit_device(
    payload: DeviceAdmissionRequest,
    request: Request,
    response: Response,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> DeviceAdmissionResult:
    try:
        result = await _service(request).admit_device(
            payload,
            hub_authorization=authorization or "",
        )
    except AuthorityFailure as exc:
        _raise(exc)
    if result.outcome == "retry_required":
        response.status_code = 202
    elif result.outcome == "blocked":
        failed = next(
            (step.failure for step in reversed(result.steps) if step.failure), None
        )
        response.status_code = {
            "unauthorized": 401,
            "forbidden": 403,
            "not_found": 404,
            "conflict": 409,
            "invalid_request": 422,
            "configuration": 503,
            "contract_violation": 502,
        }.get(failed.kind if failed else "", 502)
    return result
