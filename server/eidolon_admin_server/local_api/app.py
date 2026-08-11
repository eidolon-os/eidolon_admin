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
from typing import Annotated, Any, Literal

from fastapi import FastAPI, Header, HTTPException, Path, status
from pydantic import BaseModel, ConfigDict, Field

from ..bootstrap.control import BootstrapControlClient, BootstrapControlError
from .auth import LocalControllerSessionStore
from .config import LocalApiSettings, load_local_api_settings
from .host_services import (
    AdminHostServicesClient,
    AdminHostServicesPort,
    HostServiceControlError,
    LocalHostServiceInventoryView,
    LocalHostServiceMutationView,
    MutationOperation,
    host_service_inventory,
    host_service_mutation,
)
from .devices import (
    AdminOwnerDevicesClient,
    AdminOwnerDevicesPort,
    DeviceInventoryError,
    LocalDeviceInventoryView,
    LocalDeviceView,
    owner_device_inventory_view,
)
from .device_admissions import (
    AdminDeviceAdmissionClient,
    AdminDeviceAdmissionPort,
    DeviceAdmissionError,
    LocalDeviceAdmissionProgress,
    LocalDeviceApprovalRequest,
    LocalDeviceOnboardingTarget,
    LocalPendingDeviceEnrollmentPage,
    device_admission_progress,
    pending_device_enrollment_page,
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


class HostServiceMutationRequest(BaseModel):
    """Compare-and-swap intent, carried unchanged down to eidolond."""

    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)


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
    setup_code: str = Field(pattern=r"^[0-9]{6}$")
    controller: DevelopmentControllerClaim


def create_app(
    settings: LocalApiSettings | None = None,
    *,
    workspace_client: AdminWorkspacePort | None = None,
    runtime_client: AdminOwnerRuntimePort | None = None,
    devices_client: AdminOwnerDevicesPort | None = None,
    device_admission_client: AdminDeviceAdmissionPort | None = None,
    host_services_client: AdminHostServicesPort | None = None,
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
    )
    owns_device_admission_client = device_admission_client is None
    host_services = host_services_client or AdminHostServicesClient(
        base_url=resolved.admin_base_url,
        service_token=resolved.admin_service_token,
        timeout_seconds=resolved.admin_timeout_seconds,
    )
    owns_host_services_client = host_services_client is None

    owned_clients = [
        client
        for owned, client in (
            (owns_workspace_client, workspace),
            (owns_runtime_client, runtime),
            (owns_devices_client, devices),
            (owns_device_admission_client, device_admission),
            (owns_host_services_client, host_services),
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
                    "already_claimed": status.HTTP_409_CONFLICT,
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

    @app.get(
        "/api/local/v1/workspace/runtime",
        response_model=WorkspaceRuntimeView,
    )
    async def get_workspace_runtime(
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> WorkspaceRuntimeView:
        principal, _session = await authenticated_controller(authorization)
        owner_id = principal.get("owner_id")
        if not isinstance(owner_id, str) or not owner_id:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Host Workspace is not initialized",
            )
        operation_id = await operation_id_for_host()
        try:
            workspace_operation = await workspace.get(operation_id)
        except WorkspaceSetupError as exc:
            code = (
                status.HTTP_409_CONFLICT
                if exc.status_code == status.HTTP_404_NOT_FOUND
                else exc.status_code
            )
            raise HTTPException(code, str(exc)) from exc
        try:
            runtime_snapshot = await runtime.get_owner_primary_runtime(owner_id)
            return workspace_runtime_view(
                workspace=workspace_operation,
                runtime=runtime_snapshot,
                bound_owner_id=owner_id,
            )
        except WorkspaceRuntimeError as exc:
            code = (
                status.HTTP_409_CONFLICT
                if exc.status_code == status.HTTP_404_NOT_FOUND
                else exc.status_code
            )
            raise HTTPException(code, str(exc)) from exc

    async def owner_device_inventory(
        authorization: str | None,
    ) -> LocalDeviceInventoryView:
        principal, _session = await authenticated_controller(authorization)
        owner_id = principal.get("owner_id")
        if not isinstance(owner_id, str) or not owner_id:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Host Workspace is not initialized",
            )
        try:
            mounts = await devices.list_mounts(owner_id)
            return owner_device_inventory_view(
                mounts=mounts,
                bound_owner_id=owner_id,
            )
        except DeviceInventoryError as exc:
            raise HTTPException(exc.status_code, str(exc)) from exc

    @app.get(
        "/api/local/v1/devices",
        response_model=LocalDeviceInventoryView,
    )
    async def get_devices(
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> LocalDeviceInventoryView:
        return await owner_device_inventory(authorization)

    @app.get(
        "/api/local/v1/devices/{device_id}",
        response_model=LocalDeviceView,
    )
    async def get_device(
        device_id: str,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> LocalDeviceView:
        inventory = await owner_device_inventory(authorization)
        device = next(
            (item for item in inventory.devices if item.device_id == device_id),
            None,
        )
        if device is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Device is not mounted")
        return device

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

    @app.get(
        "/api/local/v1/device-enrollments/pending",
        response_model=LocalPendingDeviceEnrollmentPage,
    )
    async def list_pending_device_enrollments(
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> LocalPendingDeviceEnrollmentPage:
        principal, _session = await authenticated_controller(authorization)
        _owner_id, controller_id = _owner_principal(principal)
        try:
            page = await device_admission.list_pending(controller_id=controller_id)
            return pending_device_enrollment_page(page)
        except DeviceAdmissionError as exc:
            raise HTTPException(exc.status_code, str(exc)) from exc

    @app.post(
        "/api/local/v1/device-enrollments/{device_id}/approval",
        response_model=LocalDeviceAdmissionProgress,
    )
    async def approve_device_enrollment(
        device_id: Annotated[
            str,
            Path(
                min_length=1,
                max_length=128,
                pattern=r"^[A-Za-z0-9._:-]+$",
            ),
        ],
        payload: LocalDeviceApprovalRequest,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> LocalDeviceAdmissionProgress:
        principal, _session = await authenticated_controller(authorization)
        owner_id, controller_id = _owner_principal(principal)
        try:
            result = await device_admission.claim(
                payload=payload.to_admin(
                    device_id=device_id,
                    owner_id=owner_id,
                    controller_id=controller_id,
                ),
            )
            return device_admission_progress(
                owner_id=owner_id,
                companion_id=payload.companion_id,
                result=result,
            )
        except DeviceAdmissionError as exc:
            raise HTTPException(exc.status_code, str(exc)) from exc

    @app.get(
        "/api/local/v1/host/services",
        response_model=LocalHostServiceInventoryView,
    )
    async def get_host_services(
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> LocalHostServiceInventoryView:
        principal, _session = await authenticated_controller(authorization)
        _owner_principal(principal)
        try:
            return host_service_inventory(await host_services.list_services())
        except HostServiceControlError as exc:
            raise HTTPException(exc.status_code, str(exc)) from exc

    @app.post(
        "/api/local/v1/host/services/{service_id}/{operation}",
        response_model=LocalHostServiceMutationView,
    )
    async def mutate_host_service(
        service_id: str,
        operation: MutationOperation,
        payload: HostServiceMutationRequest,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> LocalHostServiceMutationView:
        principal, _session = await authenticated_controller(authorization)
        _owner_principal(principal)
        try:
            return host_service_mutation(
                await host_services.mutate(
                    service_id=service_id,
                    operation=operation,
                    expected_revision=payload.expected_revision,
                )
            )
        except HostServiceControlError as exc:
            raise HTTPException(exc.status_code, str(exc)) from exc

    @app.get("/api/local/v1/system/state")
    async def system_state() -> dict:
        result = await request_bootstrap("health")
        return {
            "status": result["status"],
            "mode": result["mode"],
            "state": result["state"],
        }

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
