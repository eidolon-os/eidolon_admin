"""HTTP interface for Admin-owned control-plane orchestration."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from eidolon_sdk.biz.system_data import CompanionRuntimeSnapshot
from eidolon_sdk.device_foundation.v1 import (
    BusinessOwnerId,
    ClaimPage,
    EnrollmentProposalPage,
    EnrollmentRecoveryProjection,
)

from .contracts import (
    BoundaryCapabilities,
    AdmissionDecisionWorkflowResult,
    ControllerClaimQuery,
    ControllerCompanionAttachment,
    ControllerEnrollmentDecisionIntent,
    ControllerEnrollmentQuery,
    ControllerEnrollmentRecoveryQuery,
    CompanionFace,
    CompanionIdentity,
    CompanionRenameRequest,
    PersonaChapter,
    PersonaRestoreRequest,
    PersonaTimeline,
    KernelMount,
    KernelMountPage,
    OwnerIdentity,
    OwnerRenameRequest,
    OperatorDeviceAdmissionRequest,
    OperatorDeviceAdmissionResult,
    OperatorOwnerDeviceInventory,
    WorkspaceInitializeRequest,
    WorkspaceOperation,
)
from eidolon_admin_server.app.service_auth import require_local_api_credential

from .service import ControlPlaneService

#: Every route mounted here requires the Local API service credential, and it
#: is required *by the router* rather than by each handler. The reason is
#: historical and worth keeping visible: this family used to authenticate per
#: handler, 12 of 23 routes never called the check, and among them were the
#: routes that rename an Owner and replace a Companion's face. Authentication
#: that a new route can forget will eventually be forgotten.
#:
#: This is the internal orchestration plane (plan §3.1). No browser reaches it;
#: the credential isolation the two-process split is bought with (§3.4.1) is
#: only real if this boundary actually checks.
router = APIRouter(
    prefix="/control-plane/v1",
    tags=["control-plane"],
    dependencies=[Depends(require_local_api_credential)],
)

# Browser-facing operator orchestration. This is a first-class current control-
# plane surface: Hub authorizes the supplied credential against canonical
# Admission/Claim endpoints, while the internal router below remains protected
# by the Local API service credential.
operator_router = APIRouter(
    prefix="/operator/v1/control-plane",
    tags=["control-plane-operator"],
)


def _service(request: Request) -> ControlPlaneService:
    return request.app.state.control_plane


@operator_router.get(
    "/owners/{owner_id}/inventory",
    response_model=OperatorOwnerDeviceInventory,
)
async def operator_owner_inventory(
    owner_id: str,
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> OperatorOwnerDeviceInventory:
    try:
        business_owner_id = BusinessOwnerId(owner_id)
    except ValueError as exc:
        raise HTTPException(422, "Owner ID must use the owner_ namespace") from exc
    return await _service(request).operator_owner_inventory(
        owner_id=str(business_owner_id),
        hub_authorization=authorization or "",
    )


@operator_router.put(
    "/device-admissions/{device_id}",
    response_model=OperatorDeviceAdmissionResult,
)
async def operator_device_admission(
    device_id: str,
    payload: OperatorDeviceAdmissionRequest,
    request: Request,
    response: Response,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> OperatorDeviceAdmissionResult:
    if payload.device_id != device_id:
        raise HTTPException(409, "Device path and request do not match")
    result = await _service(request).admit_operator_device(
        payload,
        hub_authorization=authorization or "",
    )
    if result.outcome == "retry_required":
        response.status_code = 202
    elif result.outcome == "blocked":
        response.status_code = 409
    return result


@router.get("/capabilities", response_model=BoundaryCapabilities)
async def capabilities(request: Request) -> BoundaryCapabilities:
    return _service(request).capabilities()


@router.get("/companions/{companion_id}", response_model=CompanionIdentity)
async def get_companion(companion_id: str, request: Request) -> CompanionIdentity:
    return await _service(request).data.get_companion(companion_id)


@router.patch("/companions/{companion_id}", response_model=CompanionIdentity)
async def rename_companion(
    companion_id: str,
    payload: CompanionRenameRequest,
    request: Request,
) -> CompanionIdentity:
    return await _service(request).data.rename_companion(
        companion_id,
        payload.display_name,
    )


@router.get("/companions/{companion_id}/face-state", response_model=CompanionFace)
async def companion_face_state(companion_id: str, request: Request) -> CompanionFace:
    return await _service(request).data.get_companion_face_state(companion_id)


@router.get(
    "/companions/{companion_id}/face",
    response_class=Response,
    responses={200: {"content": {"image/jpeg": {}}}, 204: {"description": "No face"}},
)
async def companion_face(companion_id: str, request: Request) -> Response:
    face = await _service(request).data.get_companion_face(companion_id)
    if face is None:
        return Response(status_code=204)
    return Response(content=face, media_type="image/jpeg")


@router.put("/companions/{companion_id}/face", response_model=CompanionFace)
async def set_companion_face(companion_id: str, request: Request) -> CompanionFace:
    return await _service(request).data.set_companion_face(
        companion_id,
        await request.body(),
    )


@router.delete("/companions/{companion_id}/face", response_model=CompanionFace)
async def clear_companion_face(companion_id: str, request: Request) -> CompanionFace:
    return await _service(request).data.clear_companion_face(companion_id)


@router.get(
    "/companions/{companion_id}/persona-timeline",
    response_model=PersonaTimeline,
)
async def persona_timeline(companion_id: str, request: Request) -> PersonaTimeline:
    return await _service(request).data.get_persona_timeline(companion_id)


@router.post(
    "/companions/{companion_id}/persona-restorations",
    response_model=PersonaChapter,
)
async def restore_persona(
    companion_id: str,
    payload: PersonaRestoreRequest,
    request: Request,
) -> PersonaChapter:
    return await _service(request).data.restore_persona(
        companion_id,
        payload.genome_id,
        payload.change_summary,
    )


@router.put(
    "/workspace-onboarding/operations/{operation_id}",
    response_model=WorkspaceOperation,
)
async def initialize_workspace(
    operation_id: UUID,
    payload: WorkspaceInitializeRequest,
    request: Request,
) -> WorkspaceOperation:
    return await _service(request).initialize_workspace(
        operation_id=str(operation_id),
        payload=payload,
    )


@router.get(
    "/workspace-onboarding/operations/{operation_id}",
    response_model=WorkspaceOperation,
)
async def get_workspace_operation(
    operation_id: UUID,
    request: Request,
) -> WorkspaceOperation:
    return await _service(request).get_workspace_operation(str(operation_id))


@router.get("/owners/{owner_id}", response_model=OwnerIdentity)
async def get_owner(owner_id: str, request: Request) -> OwnerIdentity:
    return await _service(request).workspace.get_owner(owner_id)


@router.patch("/owners/{owner_id}", response_model=OwnerIdentity)
async def rename_owner(
    owner_id: str,
    payload: OwnerRenameRequest,
    request: Request,
) -> OwnerIdentity:
    return await _service(request).workspace.rename_owner(
        owner_id,
        payload.display_name,
    )


@router.get(
    "/owners/{owner_id}/default-runtime-snapshot",
    response_model=CompanionRuntimeSnapshot,
)
async def get_owner_default_runtime(
    owner_id: str,
    request: Request,
) -> CompanionRuntimeSnapshot:
    return await _service(request).get_owner_default_runtime(owner_id)


@router.get(
    "/owners/{owner_id}/device-mounts",
    response_model=KernelMountPage,
)
async def get_owner_device_mounts(
    owner_id: str,
    request: Request,
) -> KernelMountPage:
    """Narrow product projection used only by the loopback Local API."""

    return await _service(request).list_owner_device_mounts(owner_id)


@router.put(
    "/owners/{owner_id}/device-mounts/{device_id}/companion",
    response_model=KernelMount,
)
async def set_device_companion(
    owner_id: str,
    device_id: str,
    payload: ControllerCompanionAttachment,
    request: Request,
) -> KernelMount:
    if payload.owner_id != owner_id or payload.device_id != device_id:
        raise HTTPException(409, "Attachment path and command do not match")
    return await _service(request).set_device_companion(payload=payload)


@router.post("/admission/enrollment-queries", response_model=EnrollmentProposalPage)
async def query_enrollment_recovery(
    payload: ControllerEnrollmentQuery, request: Request
) -> EnrollmentProposalPage:
    return await _service(request).list_enrollment_recovery(payload=payload)


@router.post(
    "/admission/enrollment-recoveries",
    response_model=EnrollmentRecoveryProjection,
)
async def read_enrollment_recovery(
    payload: ControllerEnrollmentRecoveryQuery, request: Request
) -> EnrollmentRecoveryProjection:
    return await _service(request).get_enrollment_recovery(payload=payload)


@router.post("/admission/claim-queries", response_model=ClaimPage)
async def query_claims(
    payload: ControllerClaimQuery, request: Request
) -> ClaimPage:
    return await _service(request).list_claims(payload=payload)


@router.put(
    "/admission/decision-intents/{enrollment_id}",
    response_model=AdmissionDecisionWorkflowResult,
)
async def submit_enrollment_decision(
    enrollment_id: str,
    payload: ControllerEnrollmentDecisionIntent,
    request: Request,
) -> AdmissionDecisionWorkflowResult:
    if payload.decision.enrollment_id != enrollment_id:
        raise HTTPException(409, "Enrollment path and Decision do not match")
    return await _service(request).decide_controller_enrollment(payload=payload)
