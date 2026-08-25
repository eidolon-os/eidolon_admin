"""Minimal local product API.

Public Host routes, Host proof, and Controller-authenticated short-lived
sessions exist. Workspace Setup is the first product mutation and Workspace
Runtime is the first daily-use projection. Both are Controller-authenticated
and reach Data only through Admin's exact loopback contracts. Mounted Device
membership follows the same boundary: Owner scope comes from the Controller
principal and the Local API consumes a narrow, service-authenticated Admin
projection rather than accepting Hub credentials from Mobile.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated, Any, Literal

import httpx
from eidolon_sdk.device_foundation.v1 import (
    BusinessOwnerId,
    ClaimPage,
    ClaimQuery,
    ClaimState,
    AdmissionListCursor,
    EnrollmentProposalPage,
    EnrollmentProposalQuery,
    EnrollmentProposalState,
    EnrollmentRecoveryProjection,
    OwnerDomainId,
)

from fastapi import (
    FastAPI,
    Header,
    HTTPException,
    Path,
    Query,
    Request,
    Response,
    status,
)
from pydantic import BaseModel, ConfigDict, Field

from ..bootstrap.control import BootstrapControlClient, BootstrapControlError
from ..bootstrap.domain import SETUP_CODE_DIGITS
from .auth import LocalControllerSessionStore
from .management.backend import AdminManagementClient
from .management.router import (
    ControllerDirectoryPort,
    ManagementBackendError,
    ManagementBackendPort,
    OwnerDevicePort,
    refusal_for_status,
    refuse,
    register_management_routes,
)
from ..app.control_plane.contracts import AdmissionDecisionWorkflowResult
from .config import LocalApiSettings, load_local_api_settings
from .host_services import (
    AdminHostServicesClient,
    AdminHostServicesPort,
    HostServiceControlError,
    HostServiceInventoryView,
    HostVitalsView,
    HostServiceMutationView,
    MutationOperation,
    host_service_inventory,
    host_vitals,
    host_service_mutation,
)
from .devices import (
    AdminOwnerDevicesClient,
    AdminOwnerDevicesPort,
    DeviceInventoryError,
    ControllerCompanionAttachment,
    LocalCompanionAttachmentRequest,
    LocalDeviceInventoryView,
    LocalDeviceView,
    owner_device_inventory_view,
)
from ..app.control_plane.contracts import ControllerDeviceRemovalRequest
from .device_admissions import (
    AdminDeviceAdmissionClient,
    AdminDeviceAdmissionPort,
    DeviceAdmissionError,
    LocalEnrollmentDecisionRequest,
    LocalDeviceOnboardingTarget,
    LocalDeviceRemovalProgress,
    LocalDeviceRemovalRequest,
    claim_query,
    device_admission_detail,
    device_admission_reason,
    device_removal_progress,
    enrollment_query,
    enrollment_recovery_query,
)
from .runtime import (
    AdminOwnerRuntimeClient,
    AdminOwnerRuntimePort,
    WorkspaceRuntimeError,
    WorkspaceRuntimeView,
    workspace_runtime_view,
)
from .workspace import (
    AdminWorkspaceClient,
    AdminWorkspacePort,
    WorkspaceSetupError,
    WorkspaceSetupRequest,
    host_workspace_operation_id,
    resolve_workspace_setup,
    workspace_status,
)


class HostProofRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"]
    challenge: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")


class ControllerChallengeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"]
    controller_id: str = Field(pattern=r"^ectrl-[0-9a-f]{20}$")


class ControllerProofRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"]
    purpose: Literal["eidolon-controller-local-auth-v1"]
    controller_id: str = Field(pattern=r"^ectrl-[0-9a-f]{20}$")
    challenge: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    reset_epoch: int = Field(ge=0)
    signature: str = Field(pattern=r"^[A-Za-z0-9_-]{8,256}$")


class DevelopmentControllerClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    controller_id: str = Field(pattern=r"^ectrl-[0-9a-f]{20}$")
    public_key: str = Field(pattern=r"^[A-Za-z0-9_-]{32,1024}$")
    display_name: str = Field(min_length=1, max_length=80)
    platform: Literal["android", "ios"]


class DevelopmentLanCommissioningClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"]
    commissioning_id: str = Field(
        pattern=(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
            r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
        )
    )
    setup_code: str = Field(pattern=rf"^[0-9]{{{SETUP_CODE_DIGITS}}}$")
    controller: DevelopmentControllerClaim


def create_app(
    settings: LocalApiSettings | None = None,
    *,
    workspace_client: AdminWorkspacePort | None = None,
    runtime_client: AdminOwnerRuntimePort | None = None,
    devices_client: AdminOwnerDevicesPort | None = None,
    device_admission_client: AdminDeviceAdmissionPort | None = None,
    host_services_client: AdminHostServicesPort | None = None,
    management_backend: ManagementBackendPort | None = None,
    controller_directory: ControllerDirectoryPort | None = None,
    owner_device_port: OwnerDevicePort | None = None,
) -> FastAPI:
    resolved = settings or load_local_api_settings()
    workspace = workspace_client or AdminWorkspaceClient(
        base_url=resolved.admin_base_url,
        service_token=resolved.admin_service_token,
        timeout_seconds=resolved.admin_timeout_seconds,
    )
    owns_workspace_client = workspace_client is None
    runtime = runtime_client or AdminOwnerRuntimeClient(
        base_url=resolved.admin_base_url,
        service_token=resolved.admin_service_token,
        timeout_seconds=resolved.admin_timeout_seconds,
    )
    owns_runtime_client = runtime_client is None
    devices = devices_client or AdminOwnerDevicesClient(
        base_url=resolved.admin_base_url,
        service_token=resolved.admin_service_token,
        timeout_seconds=resolved.admin_timeout_seconds,
    )
    owns_devices_client = devices_client is None
    device_admission = device_admission_client or AdminDeviceAdmissionClient(
        base_url=resolved.admin_base_url,
        service_token=resolved.admin_service_token,
        timeout_seconds=resolved.admin_timeout_seconds,
        workflow_socket_path=resolved.lifecycle_workflow_socket,
    )
    owns_device_admission_client = device_admission_client is None
    host_services = host_services_client or AdminHostServicesClient(
        base_url=resolved.admin_base_url,
        service_token=resolved.admin_service_token,
        timeout_seconds=resolved.admin_timeout_seconds,
    )
    owns_host_services_client = host_services_client is None
    management = management_backend or AdminManagementClient(
        base_url=resolved.admin_base_url,
        service_token=resolved.admin_service_token,
        client=httpx.AsyncClient(),
        timeout_seconds=resolved.admin_timeout_seconds,
    )
    owns_management_client = management_backend is None

    owned_clients = [
        client
        for owned, client in (
            (owns_workspace_client, workspace),
            (owns_runtime_client, runtime),
            (owns_devices_client, devices),
            (owns_device_admission_client, device_admission),
            (owns_host_services_client, host_services),
            (owns_management_client, management),
        )
        if owned
    ]

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            # Every client this app created is closed, even if an earlier one raises.
            failures: list[BaseException] = []
            for client in owned_clients:
                try:
                    await client.close()
                except Exception as exc:  # noqa: BLE001 - report all, hide none
                    failures.append(exc)
            if failures:
                raise ExceptionGroup("Local API client shutdown failed", failures)

    app = FastAPI(
        title="Eidolon Local API",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    client = BootstrapControlClient(resolved.bootstrap.control_socket)
    sessions = LocalControllerSessionStore(
        ttl_seconds=resolved.session_ttl_seconds,
    )

    async def request_bootstrap(
        operation: str,
        *,
        authentication: bool = False,
        development_commissioning: bool = False,
        **parameters: Any,
    ) -> dict:
        try:
            return await client.request(operation, **parameters)
        except BootstrapControlError as exc:
            if authentication and exc.code == "ControllerAuthenticationRejected":
                raise HTTPException(
                    status.HTTP_401_UNAUTHORIZED,
                    "Controller authentication failed",
                ) from exc
            if development_commissioning:
                code = {
                    "commissioning_denied": status.HTTP_401_UNAUTHORIZED,
                    "operation_conflict": status.HTTP_409_CONFLICT,
                    "invalid_controller_key": status.HTTP_422_UNPROCESSABLE_CONTENT,
                    "invalid_request": status.HTTP_422_UNPROCESSABLE_CONTENT,
                    "BootstrapOperationRejected": status.HTTP_404_NOT_FOUND,
                }.get(exc.code, status.HTTP_503_SERVICE_UNAVAILABLE)
                raise HTTPException(code, str(exc)) from exc
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "bootstrap control plane unavailable",
            ) from exc
        except (ConnectionError, FileNotFoundError, OSError) as exc:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "bootstrap control plane unavailable",
            ) from exc

    @app.get("/healthz")
    async def health() -> dict:
        result = await request_bootstrap("health")
        return {"status": "ok", "bootstrap": result["status"]}

    @app.get("/api/local/v1/descriptor")
    async def descriptor() -> dict:
        return await request_bootstrap("descriptor")

    @app.get("/api/local/v1/host")
    async def host_overview() -> dict:
        result = await request_bootstrap("health")
        return {
            "contract_version": "1",
            "status": result["status"],
            "mode": result["mode"],
            "descriptor": result["descriptor"],
            "state": result["state"],
        }

    @app.post("/api/local/v1/host/proof")
    async def host_proof(request: HostProofRequest) -> dict:
        return await request_bootstrap("host.prove", challenge=request.challenge)

    @app.get("/api/local/v1/development/commissioning/endpoint")
    async def development_commissioning_endpoint() -> dict:
        return await request_bootstrap(
            "dev.lan.endpoint",
            development_commissioning=True,
        )

    @app.put("/api/local/v1/development/commissioning/claim")
    async def development_commissioning_claim(
        request: DevelopmentLanCommissioningClaimRequest,
    ) -> dict:
        return await request_bootstrap(
            "dev.lan.claim",
            development_commissioning=True,
            commissioning_id=request.commissioning_id,
            setup_code=request.setup_code,
            controller=request.controller.model_dump(),
        )

    @app.post("/api/local/v1/auth/challenges")
    async def controller_challenge(request: ControllerChallengeRequest) -> dict:
        return await request_bootstrap(
            "controller.challenge",
            authentication=True,
            controller_id=request.controller_id,
        )

    @app.post("/api/local/v1/auth/sessions")
    async def controller_session(request: ControllerProofRequest) -> dict:
        principal = await request_bootstrap(
            "controller.authenticate",
            authentication=True,
            proof=request.model_dump(),
        )
        token, session = sessions.issue(principal)
        return {
            "contract_version": "1",
            "token_type": "Bearer",
            "access_token": token,
            "expires_at": session.expires_at.isoformat().replace("+00:00", "Z"),
            "controller": principal,
        }

    @app.get("/api/local/v1/auth/session")
    async def current_controller_session(
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> dict:
        principal, session = await authenticated_controller(authorization)
        return {
            "contract_version": "1",
            "expires_at": session.expires_at.isoformat().replace("+00:00", "Z"),
            "controller": principal,
        }

    async def authenticated_controller(
        authorization: str | None,
    ) -> tuple[dict, Any]:
        token = _bearer_token(authorization)
        session = sessions.get(token)
        if session is None:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                "Controller session is missing or expired",
            )
        try:
            principal = await request_bootstrap(
                "controller.validate",
                authentication=True,
                controller_id=session.controller_id,
                reset_epoch=session.reset_epoch,
            )
        except HTTPException as exc:
            if exc.status_code == status.HTTP_401_UNAUTHORIZED:
                sessions.revoke(session)
            raise
        return principal, session

    async def operation_id_for_host() -> str:
        host = await request_bootstrap("descriptor")
        try:
            return host_workspace_operation_id(host["host_id"])
        except (KeyError, ValueError) as exc:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "bootstrap Host identity is unavailable",
            ) from exc

    @app.get("/api/local/v1/setup/workspace")
    async def get_workspace(
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> dict:
        principal, _session = await authenticated_controller(authorization)
        operation_id = await operation_id_for_host()
        owner_id = principal.get("owner_id")
        if owner_id is None:
            return workspace_status(operation_id=operation_id, result=None)
        try:
            result = await workspace.get(operation_id)
        except WorkspaceSetupError as exc:
            raise HTTPException(exc.status_code, str(exc)) from exc
        if result.owner.owner_id != owner_id:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Host Owner scope does not match its Data workspace",
            )
        return workspace_status(operation_id=operation_id, result=result)

    @app.put("/api/local/v1/setup/workspace")
    async def initialize_workspace(
        payload: WorkspaceSetupRequest,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> dict:
        principal, _session = await authenticated_controller(authorization)
        operation_id = await operation_id_for_host()
        try:
            result = await resolve_workspace_setup(
                workspace,
                operation_id=operation_id,
                payload=payload.to_admin(),
                bound_owner_id=principal.get("owner_id"),
            )
        except WorkspaceSetupError as exc:
            raise HTTPException(exc.status_code, str(exc)) from exc
        bound = await request_bootstrap(
            "controller.bind_owner",
            authentication=True,
            controller_id=principal["controller_id"],
            reset_epoch=principal["reset_epoch"],
            owner_id=result.owner.owner_id,
        )
        if bound.get("owner_id") != result.owner.owner_id:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "bootstrap did not confirm the Data Owner scope",
            )
        return workspace_status(operation_id=operation_id, result=result)

    async def _owned_companion(
        companion_id: str,
        authorization: str | None,
    ) -> str:
        """The Owner of this session, having proved this Companion is theirs.

        Every Companion-scoped route goes through here. An identifier in a
        path is not authority, and whose Companion it is can only be judged
        where whose session this is is known — so it is judged once, and the
        routes below cannot drift into judging it differently.
        """

        principal, _session = await authenticated_controller(authorization)
        owner_id = principal.get("owner_id")
        if not isinstance(owner_id, str) or not owner_id:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Host Workspace is not initialized",
            )
        try:
            existing = await runtime.get_companion(companion_id)
        except WorkspaceRuntimeError as exc:
            raise HTTPException(exc.status_code, str(exc)) from exc
        if existing.owner_id != owner_id:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                "Companion does not exist",
            )
        return owner_id

    async def owner_device_inventory(
        authorization: str | None,
    ) -> LocalDeviceInventoryView:
        principal, _session = await authenticated_controller(authorization)
        owner_id, controller_id = _owner_principal(principal)
        owner_domain_id, business_owner_id = _admission_scope(owner_id)
        try:
            mounts = await devices.list_mounts(owner_id)
            claims = await device_admission.query_claims(
                payload=claim_query(
                    controller_id=controller_id,
                    owner_domain_id=owner_domain_id,
                    business_owner_id=business_owner_id,
                    query=ClaimQuery(
                        owner_domain_id=owner_domain_id,
                        states=(ClaimState.ACTIVE, ClaimState.SUSPENDED, ClaimState.REVOKED),
                        cursor=None,
                        limit=200,
                    ),
                )
            )
            return owner_device_inventory_view(
                mounts=mounts,
                bound_owner_id=owner_id,
                claims=claims,
            )
        except (DeviceInventoryError, DeviceAdmissionError) as exc:
            raise HTTPException(exc.status_code, str(exc)) from exc

    def _admission_scope(owner_id: str) -> tuple[OwnerDomainId, BusinessOwnerId]:
        target = resolved.device_onboarding_target
        if target is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Hub Device onboarding target is not configured",
            )
        try:
            return OwnerDomainId(target.owner_domain_id), BusinessOwnerId(owner_id)
        except ValueError as exc:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Host Owner Domain or business Owner identity is invalid",
            ) from exc

    @app.get(
        "/api/local/v1/device-onboarding/target",
        response_model=LocalDeviceOnboardingTarget,
    )
    async def get_device_onboarding_target(
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> LocalDeviceOnboardingTarget:
        principal, _session = await authenticated_controller(authorization)
        _owner_principal(principal)
        target = resolved.device_onboarding_target
        if target is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Hub Device onboarding target is not configured",
            )
        return LocalDeviceOnboardingTarget.from_verified(target)

    def _admission_cursor(
        owner_domain_id: OwnerDomainId,
        after_sort_key: str | None,
        after_resource_id: str | None,
    ) -> AdmissionListCursor | None:
        """The caller's page position, or nothing — never half of one.

        A cursor with one half missing would silently read page one again, so a
        client that lost a field learns it here instead of looping forever.
        """

        if after_sort_key is None and after_resource_id is None:
            return None
        if after_sort_key is None or after_resource_id is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "A page cursor needs both its sort key and its resource ID",
            )
        try:
            return AdmissionListCursor(
                owner_domain_id=owner_domain_id,
                sort_key=after_sort_key,
                resource_id=after_resource_id,
            )
        except ValueError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, "Page cursor is invalid"
            ) from exc

    @app.get(
        "/api/local/v1/device-enrollments",
        response_model=EnrollmentProposalPage,
    )
    async def list_device_enrollments(
        states: Annotated[list[EnrollmentProposalState], Query()] = [
            EnrollmentProposalState.PENDING_REVIEW,
            EnrollmentProposalState.APPROVED_AWAITING_HANDOFF,
            EnrollmentProposalState.GRANT_DELIVERED,
            EnrollmentProposalState.GRANT_ACKNOWLEDGED,
        ],
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
        after_sort_key: Annotated[str | None, Query(max_length=64)] = None,
        after_resource_id: Annotated[str | None, Query(max_length=128)] = None,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> EnrollmentProposalPage:
        principal, _session = await authenticated_controller(authorization)
        owner_id, controller_id = _owner_principal(principal)
        owner_domain_id, business_owner_id = _admission_scope(owner_id)
        cursor = _admission_cursor(owner_domain_id, after_sort_key, after_resource_id)
        try:
            return await device_admission.query_enrollments(
                payload=enrollment_query(
                    controller_id=controller_id,
                    owner_domain_id=owner_domain_id,
                    business_owner_id=business_owner_id,
                    query=EnrollmentProposalQuery(
                        owner_domain_id=owner_domain_id,
                        states=tuple(states),
                        cursor=cursor,
                        limit=limit,
                    ),
                )
            )
        except DeviceAdmissionError as exc:
            raise HTTPException(
                exc.status_code, device_admission_detail(exc)
            ) from exc

    @app.get(
        "/api/local/v1/device-enrollments/{enrollment_id}",
        response_model=EnrollmentRecoveryProjection,
    )
    async def read_device_enrollment(
        enrollment_id: Annotated[
            str,
            Path(
                min_length=1,
                max_length=128,
                pattern=r"^[A-Za-z0-9._:-]+$",
            ),
        ],
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> EnrollmentRecoveryProjection:
        """What became of one Enrollment, as the Admission Authority holds it.

        A Controller that commissioned a device watches this rather than its own
        local guess: the device, not the phone, drives Grant collection, and the
        phone may have been closed while it happened.
        """

        principal, _session = await authenticated_controller(authorization)
        owner_id, controller_id = _owner_principal(principal)
        owner_domain_id, business_owner_id = _admission_scope(owner_id)
        try:
            return await device_admission.recover_enrollment(
                payload=enrollment_recovery_query(
                    controller_id=controller_id,
                    owner_domain_id=owner_domain_id,
                    business_owner_id=business_owner_id,
                    enrollment_id=enrollment_id,
                )
            )
        except DeviceAdmissionError as exc:
            raise HTTPException(
                exc.status_code, device_admission_detail(exc)
            ) from exc

    @app.get(
        "/api/local/v1/device-claims",
        response_model=ClaimPage,
    )
    async def list_device_claims(
        states: Annotated[list[ClaimState], Query()] = [
            ClaimState.ACTIVE,
            ClaimState.SUSPENDED,
            ClaimState.REVOKED,
        ],
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
        after_sort_key: Annotated[str | None, Query(max_length=64)] = None,
        after_resource_id: Annotated[str | None, Query(max_length=128)] = None,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> ClaimPage:
        principal, _session = await authenticated_controller(authorization)
        owner_id, controller_id = _owner_principal(principal)
        owner_domain_id, business_owner_id = _admission_scope(owner_id)
        cursor = _admission_cursor(owner_domain_id, after_sort_key, after_resource_id)
        try:
            return await device_admission.query_claims(
                payload=claim_query(
                    controller_id=controller_id,
                    owner_domain_id=owner_domain_id,
                    business_owner_id=business_owner_id,
                    query=ClaimQuery(
                        owner_domain_id=owner_domain_id,
                        states=tuple(states),
                        cursor=cursor,
                        limit=limit,
                    ),
                )
            )
        except DeviceAdmissionError as exc:
            raise HTTPException(exc.status_code, device_admission_detail(exc)) from exc

    @app.put(
        "/api/local/v1/device-enrollments/{enrollment_id}/decision",
        response_model=AdmissionDecisionWorkflowResult,
    )
    async def decide_device_enrollment(
        enrollment_id: Annotated[
            str,
            Path(
                min_length=1,
                max_length=128,
                pattern=r"^[A-Za-z0-9._:-]+$",
            ),
        ],
        payload: LocalEnrollmentDecisionRequest,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> AdmissionDecisionWorkflowResult:
        principal, _session = await authenticated_controller(authorization)
        owner_id, controller_id = _owner_principal(principal)
        owner_domain_id, business_owner_id = _admission_scope(owner_id)
        try:
            return await device_admission.decide(
                payload=payload.to_admin(
                    enrollment_id=enrollment_id,
                    owner_domain_id=owner_domain_id,
                    business_owner_id=business_owner_id,
                    controller_id=controller_id,
                )
            )
        except DeviceAdmissionError as exc:
            raise HTTPException(
                exc.status_code, device_admission_detail(exc)
            ) from exc

    async def _owned_device(
        device_id: str,
        authorization: str | None,
    ) -> tuple[str, str, Any, Any]:
        """This session's Owner and Controller, having proved the device is theirs.

        The device-scoped twin of _owned_companion, and added for the same
        reason: an identifier in a path is not authority. Removal took a
        device_id and an owner_id from two different places and never asked
        whether they belonged together — neither here, nor in the control
        plane, nor in the Hub use case underneath, each of which could
        reasonably assume one of the others had.

        Kernel's mounts are the answer because they are owner-scoped by
        construction. Mounts that are no longer active still count: a removal
        that half-completed must be retryable, and refusing the retry would
        strand the device in the one state that keeps it from being added
        again.
        """

        principal, session = await authenticated_controller(authorization)
        owner_id, controller_id = _owner_principal(principal)
        owner_domain_id, business_owner_id = _admission_scope(owner_id)
        try:
            page = await device_admission.query_claims(
                payload=claim_query(
                    controller_id=controller_id,
                    owner_domain_id=owner_domain_id,
                    business_owner_id=business_owner_id,
                    query=ClaimQuery(
                        owner_domain_id=owner_domain_id,
                        states=(ClaimState.ACTIVE, ClaimState.SUSPENDED, ClaimState.REVOKED),
                        cursor=None,
                        limit=200,
                    ),
                )
            )
        except DeviceAdmissionError as exc:
            raise HTTPException(exc.status_code, str(exc)) from exc
        claim = next(
            (candidate for candidate in page.items if candidate.device_ref.device_instance_id == device_id),
            None,
        )
        if claim is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Device does not exist")
        return owner_id, controller_id, session, claim.device_ref

    @app.get("/api/local/v1/system/state")
    async def system_state() -> dict:
        result = await request_bootstrap("health")
        return {
            "status": result["status"],
            "mode": result["mode"],
            "state": result["state"],
        }

    async def management_owner(authorization: str | None) -> str:
        """The Owner this Controller session speaks for, and only that one.

        The public management surface never accepts an ``owner_id``; it is
        derived here, once, from a session Bootstrap has just re-validated.

        Also the funnel every management request passes through *before* the
        surface's own routes, which makes it the place to put the refusals into
        the surface's envelope. Two of them arrive here as plain HTTP: an expired
        session from the shared Controller check, and a Host that has no Owner
        yet — and a client that reads only the status cannot tell the second from
        a lost race, because both are 409. The domain code is what separates
        them, and it is carried rather than guessed.
        """
        try:
            principal, _session = await authenticated_controller(authorization)
        except HTTPException as exc:
            raise refuse(
                exc.status_code,
                str(exc.detail),
                code="controller_session_invalid"
                if exc.status_code == status.HTTP_401_UNAUTHORIZED
                else None,
            ) from exc
        owner_id = principal.get("owner_id")
        if not isinstance(owner_id, str) or not owner_id:
            raise refuse(
                status.HTTP_409_CONFLICT,
                "Host Workspace is not initialized",
                code="host_not_provisioned",
                # Not a conflict a client can resolve by re-reading: nothing has
                # been set up yet, and the phone's move is to finish setup rather
                # than to look again or retry.
                kind="not_configured",
            )
        return owner_id

    class _Controllers:
        """The Host's own grant record, over the socket this process already uses.

        Not behind the management backend: that boundary exists so this process
        holds no *authority* credential, and a controller grant is not an
        authority's data — it is this Host's own trust root, which this process
        must already be able to reach, because it is what every request here is
        authenticated against.
        """

        async def list_controllers(self, controller_id: str) -> dict:
            return await request_bootstrap(
                "controller.list", authentication=True, controller_id=controller_id
            )

        async def invite_controller(
            self, controller_id: str, ttl_seconds: int | None
        ) -> dict:
            return await request_bootstrap(
                "controller.invite",
                authentication=True,
                controller_id=controller_id,
                ttl_seconds=ttl_seconds,
            )

        async def revoke_controller(self, controller_id: str, target_id: str) -> dict:
            return await request_bootstrap(
                "controller.revoke",
                authentication=True,
                controller_id=controller_id,
                target_id=target_id,
            )

    async def _owned_device_ref(owner_id: str, controller_id: str, device_id: str):
        """The DeviceRef of a device this Owner holds, or a refusal.

        An identifier in a path is not authority. Kernel's Claims are the answer
        because they are owner-scoped by construction, and mounts that are no
        longer active still count: a removal that half-completed must be
        retryable, or the device is stranded in the one state that keeps it from
        being added again.
        """

        owner_domain_id, business_owner_id = _admission_scope(owner_id)
        try:
            page = await device_admission.query_claims(
                payload=claim_query(
                    controller_id=controller_id,
                    owner_domain_id=owner_domain_id,
                    business_owner_id=business_owner_id,
                    query=ClaimQuery(
                        owner_domain_id=owner_domain_id,
                        states=(
                            ClaimState.ACTIVE,
                            ClaimState.SUSPENDED,
                            ClaimState.REVOKED,
                        ),
                        cursor=None,
                        limit=200,
                    ),
                )
            )
        except DeviceAdmissionError as exc:
            raise ManagementBackendError(
                str(exc),
                status_code=exc.status_code,
                refusal=refusal_for_status(exc.status_code, str(exc)),
            ) from exc
        for candidate in page.items:
            if candidate.device_ref.device_instance_id == device_id:
                return candidate.device_ref
        raise ManagementBackendError(
            "Device does not exist",
            status_code=404,
            refusal=refusal_for_status(404, "Device does not exist"),
        )

    @dataclass(frozen=True, slots=True)
    class _DeviceSession:
        """One authenticated Controller, and everything the device calls need.

        Passed whole rather than as three arguments so the management router
        never has to know that removal needs a reset epoch and an expiry — that
        is between this boundary and the admission authority.
        """

        owner_id: str
        controller_id: str
        controller_session: Any

    async def management_device_session(authorization: str | None) -> "_DeviceSession":
        principal, session = await authenticated_controller(authorization)
        owner_id, controller_id = _owner_principal(principal)
        return _DeviceSession(
            owner_id=owner_id,
            controller_id=controller_id,
            controller_session=session,
        )

    class _Devices:
        """The composed device read, from the two clients this process holds.

        It lives here for the same reason the controller list does: the
        admission authority authorises by *actor*, and the authenticated
        Controller only exists at this boundary. What it hands upward is already
        composed — Hub's Claim and Kernel's mount agreeing about one device — so
        the management router phrases it and does not merge it.
        """

        async def list_devices(self, *, session):
            owner_id = session.owner_id
            controller_id = session.controller_id
            owner_domain_id, business_owner_id = _admission_scope(owner_id)
            try:
                mounts = await devices.list_mounts(owner_id)
                claims = await device_admission.query_claims(
                    payload=claim_query(
                        controller_id=controller_id,
                        owner_domain_id=owner_domain_id,
                        business_owner_id=business_owner_id,
                        query=ClaimQuery(
                            owner_domain_id=owner_domain_id,
                            states=(
                                ClaimState.ACTIVE,
                                ClaimState.SUSPENDED,
                                ClaimState.REVOKED,
                            ),
                            cursor=None,
                            limit=200,
                        ),
                    )
                )
                return owner_device_inventory_view(
                    mounts=mounts,
                    bound_owner_id=owner_id,
                    claims=claims,
                )
            except (DeviceInventoryError, DeviceAdmissionError) as exc:
                raise ManagementBackendError(
                    str(exc),
                    status_code=exc.status_code,
                    refusal=refusal_for_status(exc.status_code, str(exc)),
                ) from exc

        async def remove_device(self, *, session, device_id: str, request_id: str):
            """Withdraw this device's grant, having proved it is this Owner's.

            The proof is the same as everywhere else here — Kernel's mounts are
            owner-scoped by construction — and it includes mounts that are no
            longer active on purpose: a removal that half-completed must be
            retryable, or the device is stranded in the one state that keeps it
            from being added again.
            """

            owner = session.owner_id
            controller = session.controller_id
            device_ref = await _owned_device_ref(owner, controller, device_id)
            try:
                result = await device_admission.remove(
                    payload=ControllerDeviceRemovalRequest(
                        contract_version="1",
                        request_id=request_id,
                        owner_id=owner,
                        controller_id=controller,
                        device_id=device_id,
                        reason="owner-removed",
                    ),
                    controller_reset_epoch=session.controller_session.reset_epoch,
                    authorization_expires_at=session.controller_session.expires_at,
                    target_device_ref=device_ref,
                )
            except DeviceAdmissionError as exc:
                raise ManagementBackendError(
                    device_admission_detail(exc),
                    status_code=exc.status_code,
                    refusal=refusal_for_status(
                        exc.status_code, device_admission_reason(exc)
                    ),
                ) from exc
            return device_removal_progress(
                owner_id=owner, device_id=device_id, result=result
            )

        async def set_device_companion(
            self,
            *,
            session,
            device_id: str,
            companion_id: str | None,
            expected_revision: int,
            request_id: str,
        ):
            owner_id = session.owner_id
            inventory = await self.list_devices(session=session)
            if all(item.device_id != device_id for item in inventory.devices):
                # Absent rather than forbidden, and checked before the mutation:
                # a device this Owner does not hold must not be reachable by id.
                raise ManagementBackendError(
                    "Device is not mounted",
                    status_code=404,
                    refusal=refusal_for_status(404, "Device is not mounted"),
                )
            try:
                await devices.set_companion(
                    payload=ControllerCompanionAttachment(
                        contract_version="1",
                        request_id=request_id,
                        owner_id=owner_id,
                        device_id=device_id,
                        companion_id=companion_id,
                        expected_revision=expected_revision,
                    )
                )
            except DeviceInventoryError as exc:
                raise ManagementBackendError(
                    str(exc),
                    status_code=exc.status_code,
                    refusal=refusal_for_status(exc.status_code, str(exc)),
                ) from exc
            refreshed = await self.list_devices(session=session)
            for item in refreshed.devices:
                if item.device_id == device_id:
                    return item
            raise ManagementBackendError(
                "Device stopped being mounted",
                status_code=409,
                refusal=refusal_for_status(409, "Device stopped being mounted"),
            )

    async def management_controller(authorization: str | None) -> str:
        """Which phone is asking — the one thing these three routes need.

        Not the Owner: managing controllers is about who may manage this Host,
        which is a different question from whose Eidolons these are, and a
        Host with no Workspace yet still has phones that hold it.
        """
        principal, _session = await authenticated_controller(authorization)
        return principal["controller_id"]

    register_management_routes(
        app,
        backend=management,
        authenticated_owner=management_owner,
        controllers=controller_directory or _Controllers(),
        authenticated_controller_id=management_controller,
        host=host_services,
        devices=owner_device_port or _Devices(),
        authenticated_controller_session=management_device_session,
    )

    return app


def _bearer_token(value: str | None) -> str:
    if not isinstance(value, str):
        return ""
    scheme, separator, token = value.partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not token:
        return ""
    return token


def _owner_principal(principal: dict) -> tuple[str, str]:
    owner_id = principal.get("owner_id")
    controller_id = principal.get("controller_id")
    if not isinstance(owner_id, str) or not owner_id:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Host Workspace is not initialized",
        )
    if not isinstance(controller_id, str) or not controller_id:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Controller identity is unavailable",
        )
    return owner_id, controller_id
