"""HTTP interface for Admin-owned control-plane orchestration."""

from __future__ import annotations

import hmac
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request, Response
from eidolon_sdk.biz.system_data import CompanionRuntimeSnapshot

from .contracts import (
    BoundaryCapabilities,
    CompanionFace,
    CompanionIdentity,
    CompanionRenameRequest,
    DeviceRenameCommand,
    HubDevice,
    PersonaChapter,
    PersonaRestoreRequest,
    PersonaTimeline,
    ControllerDeviceAdmissionRequest,
    ControllerDeviceRemovalRequest,
    DeviceAdmissionRequest,
    DeviceAdmissionResult,
    DeviceRemovalResult,
    HubDevicePage,
    KernelMountPage,
    OwnerIdentity,
    OwnerInventory,
    OwnerRecollections,
    OwnerRenameRequest,
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


@router.patch("/companions/{companion_id}", response_model=CompanionIdentity)
async def rename_companion(
    companion_id: str,
    payload: CompanionRenameRequest,
    request: Request,
) -> CompanionIdentity:
    try:
        return await _service(request).data.rename_companion(
            companion_id,
            payload.display_name,
        )
    except AuthorityFailure as exc:
        _raise(exc)


@router.get("/companions/{companion_id}/face-state", response_model=CompanionFace)
async def companion_face_state(companion_id: str, request: Request) -> CompanionFace:
    try:
        return await _service(request).data.get_companion_face_state(companion_id)
    except AuthorityFailure as exc:
        _raise(exc)


@router.get(
    "/companions/{companion_id}/face",
    response_class=Response,
    responses={200: {"content": {"image/jpeg": {}}}, 204: {"description": "No face"}},
)
async def companion_face(companion_id: str, request: Request) -> Response:
    try:
        face = await _service(request).data.get_companion_face(companion_id)
    except AuthorityFailure as exc:
        _raise(exc)
    if face is None:
        return Response(status_code=204)
    return Response(content=face, media_type="image/jpeg")


@router.put("/companions/{companion_id}/face", response_model=CompanionFace)
async def set_companion_face(companion_id: str, request: Request) -> CompanionFace:
    try:
        return await _service(request).data.set_companion_face(
            companion_id,
            await request.body(),
        )
    except AuthorityFailure as exc:
        _raise(exc)


@router.delete("/companions/{companion_id}/face", response_model=CompanionFace)
async def clear_companion_face(companion_id: str, request: Request) -> CompanionFace:
    try:
        return await _service(request).data.clear_companion_face(companion_id)
    except AuthorityFailure as exc:
        _raise(exc)


@router.get(
    "/companions/{companion_id}/persona-timeline",
    response_model=PersonaTimeline,
)
async def persona_timeline(companion_id: str, request: Request) -> PersonaTimeline:
    try:
        return await _service(request).data.get_persona_timeline(companion_id)
    except AuthorityFailure as exc:
        _raise(exc)


@router.post(
    "/companions/{companion_id}/persona-restorations",
    response_model=PersonaChapter,
)
async def restore_persona(
    companion_id: str,
    payload: PersonaRestoreRequest,
    request: Request,
) -> PersonaChapter:
    try:
        return await _service(request).data.restore_persona(
            companion_id,
            payload.genome_id,
            payload.change_summary,
        )
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
    "/owners/{owner_id}/recollections",
    response_model=OwnerRecollections,
)
async def owner_recollections(
    owner_id: str,
    request: Request,
    q: str,
    limit: int = 10,
) -> OwnerRecollections:
    try:
        recollections = await _service(request).memory.recollections(
            owner_id=owner_id,
            query=q,
            limit=limit,
        )
    except AuthorityFailure as exc:
        _raise(exc)
    return OwnerRecollections(
        owner_id=owner_id,
        query=q,
        recollections=recollections,
    )


@router.get("/owners/{owner_id}", response_model=OwnerIdentity)
async def get_owner(owner_id: str, request: Request) -> OwnerIdentity:
    try:
        return await _service(request).workspace.get_owner(owner_id)
    except AuthorityFailure as exc:
        _raise(exc)


@router.patch("/owners/{owner_id}", response_model=OwnerIdentity)
async def rename_owner(
    owner_id: str,
    payload: OwnerRenameRequest,
    request: Request,
) -> OwnerIdentity:
    try:
        return await _service(request).workspace.rename_owner(
            owner_id,
            payload.display_name,
        )
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


@router.patch(
    "/owners/{owner_id}/devices/{device_id}/name/{controller_id}",
    response_model=HubDevice,
)
async def rename_owner_device(
    owner_id: str,
    device_id: str,
    controller_id: str,
    payload: DeviceRenameCommand,
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> HubDevice:
    _authorize_local_api(request, authorization)
    try:
        return await _service(request).rename_owner_device(
            owner_id=owner_id,
            controller_id=controller_id,
            device_id=device_id,
            display_name=payload.display_name,
        )
    except AuthorityFailure as exc:
        _raise(exc)


@router.get(
    "/owners/{owner_id}/device-inventory/{controller_id}",
    response_model=OwnerInventory,
)
async def local_owner_device_inventory(
    owner_id: str,
    controller_id: str,
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> OwnerInventory:
    """The Owner's devices as both authorities see them, for the Local API.

    Separate from the inventory route beside it because of where the Hub
    credential comes from: there, a caller supplies one; here, Admin mints it
    for this Controller, the way it does for the pending queue. A phone should
    never be holding a Hub management credential.
    """

    _authorize_local_api(request, authorization)
    try:
        return await _service(request).local_owner_inventory(
            owner_id=owner_id,
            controller_id=controller_id,
        )
    except AuthorityFailure as exc:
        _raise(exc)


@router.get(
    "/pending-device-enrollments/{controller_id}",
    response_model=HubDevicePage,
)
async def list_pending_device_enrollments(
    controller_id: str,
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> HubDevicePage:
    _authorize_local_api(request, authorization)
    if not controller_id or len(controller_id) > 128:
        raise HTTPException(422, "controller_id must contain between 1 and 128 characters")
    try:
        return await _service(request).list_pending_device_enrollments(
            controller_id=controller_id,
        )
    except AuthorityFailure as exc:
        _raise(exc)


@router.put(
    "/local-device-admissions/{device_id}",
    response_model=DeviceAdmissionResult,
)
async def admit_local_device(
    device_id: str,
    payload: ControllerDeviceAdmissionRequest,
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> DeviceAdmissionResult:
    """Service-only forward workflow consumed by the Controller Local API."""

    _authorize_local_api(request, authorization)
    if not device_id or len(device_id) > 128:
        raise HTTPException(422, "device_id must contain between 1 and 128 characters")
    if payload.device_id != device_id:
        raise HTTPException(409, "device admission path and body do not match")
    try:
        return await _service(request).admit_controller_device(
            payload=payload,
        )
    except AuthorityFailure as exc:
        _raise(exc)


@router.put(
    "/local-device-removals/{device_id}",
    response_model=DeviceRemovalResult,
)
async def remove_local_device(
    device_id: str,
    payload: ControllerDeviceRemovalRequest,
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> DeviceRemovalResult:
    """Service-only forward workflow consumed by the Controller Local API."""

    _authorize_local_api(request, authorization)
    if not device_id or len(device_id) > 128:
        raise HTTPException(422, "device_id must contain between 1 and 128 characters")
    if payload.device_id != device_id:
        raise HTTPException(409, "device removal path and body do not match")
    try:
        return await _service(request).remove_controller_device(payload=payload)
    except AuthorityFailure as exc:
        _raise(exc)
