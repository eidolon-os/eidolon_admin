"""Admin application orchestration across independent control authorities."""

from __future__ import annotations

import asyncio
import time

import httpx

from ..settings import Settings
from .clients import (
    DataAuthorityClient,
    DataWorkspaceAuthorityClient,
    HubManagementClient,
    KernelMountClient,
)
from .contracts import (
    BoundaryCapabilities,
    DeviceAdmissionRequest,
    DeviceAdmissionResult,
    OwnerInventory,
    SourceStatus,
    WorkspaceInitializeRequest,
    WorkspaceOperation,
    WorkflowStep,
)
from .directory import SystemDirectoryClient
from .errors import AuthorityFailure


def _child_request_id(workflow_id: str, suffix: str) -> str:
    return f"admin:{workflow_id}:{suffix}"


class ControlPlaneService:
    def __init__(
        self,
        *,
        directory: SystemDirectoryClient,
        data: DataAuthorityClient,
        workspace: DataWorkspaceAuthorityClient,
        hub: HubManagementClient,
        kernel: KernelMountClient,
    ) -> None:
        self.directory = directory
        self.data = data
        self.workspace = workspace
        self.hub = hub
        self.kernel = kernel

    @classmethod
    def build(
        cls,
        *,
        settings: Settings,
        http_client: httpx.AsyncClient,
    ) -> ControlPlaneService:
        directory = SystemDirectoryClient(
            base_url=settings.system_directory_url,
            uds_path=settings.system_directory_uds,
            timeout_seconds=settings.directory_timeout_seconds,
            client=None if settings.system_directory_uds else http_client,
        )
        return cls(
            directory=directory,
            data=DataAuthorityClient(
                directory=directory,
                client=http_client,
                service_token=settings.data_authority_token,
                timeout_seconds=settings.authority_timeout_seconds,
            ),
            workspace=DataWorkspaceAuthorityClient(
                directory=directory,
                client=http_client,
                service_token=settings.data_workspace_authority_token,
                timeout_seconds=settings.authority_timeout_seconds,
            ),
            hub=HubManagementClient(
                directory=directory,
                client=http_client,
                timeout_seconds=settings.authority_timeout_seconds,
            ),
            kernel=KernelMountClient(
                directory=directory,
                client=http_client,
                timeout_seconds=settings.authority_timeout_seconds,
            ),
        )

    async def initialize_workspace(
        self,
        *,
        operation_id: str,
        payload: WorkspaceInitializeRequest,
    ) -> WorkspaceOperation:
        return await self.workspace.initialize(
            operation_id=operation_id,
            payload=payload,
        )

    async def get_workspace_operation(self, operation_id: str) -> WorkspaceOperation:
        return await self.workspace.get(operation_id)

    async def close(self) -> None:
        await self.directory.close()

    async def admit_device(
        self,
        payload: DeviceAdmissionRequest,
        *,
        hub_authorization: str,
    ) -> DeviceAdmissionResult:
        hub_request_id = _child_request_id(payload.request_id, "hub-approve")
        mount_request_id = _child_request_id(payload.request_id, "kernel-mount")
        attach_request_id = _child_request_id(payload.request_id, "kernel-attach")
        hub = await self.hub.approve(
            device_id=payload.device_id,
            owner_id=payload.owner_id,
            request_id=hub_request_id,
            authorization=hub_authorization,
        )
        steps = [
            WorkflowStep(
                name="hub_approval",
                state="committed",
                request_id=hub_request_id,
            )
        ]
        try:
            mount_result = await self.kernel.mount(
                owner_id=payload.owner_id,
                device_id=payload.device_id,
                request_id=mount_request_id,
                expected_revision=payload.expected_mount_revision,
                replace_existing=payload.replace_existing_mount,
            )
        except AuthorityFailure as exc:
            steps.extend(
                (
                    WorkflowStep(
                        name="kernel_mount",
                        state="failed",
                        request_id=mount_request_id,
                        failure=exc.to_wire(),
                    ),
                    WorkflowStep(
                        name="companion_attachment",
                        state="not_attempted"
                        if payload.companion_id
                        else "not_requested",
                        request_id=attach_request_id if payload.companion_id else None,
                    ),
                )
            )
            return DeviceAdmissionResult(
                request_id=payload.request_id,
                outcome="retry_required" if exc.retryable else "blocked",
                completed_stage="hub_approved",
                recovery=(
                    "retry-forward-same-request-id"
                    if exc.retryable
                    else "operator-action-required"
                ),
                steps=tuple(steps),
                hub=hub,
            )
        steps.append(
            WorkflowStep(
                name="kernel_mount",
                state="replayed" if mount_result.replayed else "committed",
                request_id=mount_request_id,
                revision=mount_result.mount.revision,
            )
        )
        if payload.companion_id is None:
            steps.append(
                WorkflowStep(name="companion_attachment", state="not_requested")
            )
            return DeviceAdmissionResult(
                request_id=payload.request_id,
                outcome="completed",
                completed_stage="kernel_mounted",
                steps=tuple(steps),
                hub=hub,
                mount=mount_result.mount,
            )
        try:
            attach_result = await self.kernel.attach(
                owner_id=payload.owner_id,
                device_id=payload.device_id,
                companion_id=payload.companion_id,
                request_id=attach_request_id,
                expected_revision=mount_result.mount.revision,
            )
        except AuthorityFailure as exc:
            steps.append(
                WorkflowStep(
                    name="companion_attachment",
                    state="failed",
                    request_id=attach_request_id,
                    revision=mount_result.mount.revision,
                    failure=exc.to_wire(),
                )
            )
            return DeviceAdmissionResult(
                request_id=payload.request_id,
                outcome="retry_required" if exc.retryable else "blocked",
                completed_stage="kernel_mounted",
                recovery=(
                    "retry-forward-same-request-id"
                    if exc.retryable
                    else "operator-action-required"
                ),
                steps=tuple(steps),
                hub=hub,
                mount=mount_result.mount,
            )
        steps.append(
            WorkflowStep(
                name="companion_attachment",
                state="replayed" if attach_result.replayed else "committed",
                request_id=attach_request_id,
                revision=attach_result.mount.revision,
            )
        )
        return DeviceAdmissionResult(
            request_id=payload.request_id,
            outcome="completed",
            completed_stage="companion_attached",
            steps=tuple(steps),
            hub=hub,
            mount=attach_result.mount,
        )

    async def inventory(
        self, *, owner_id: str, hub_authorization: str
    ) -> OwnerInventory:
        async def measured(call):
            started = time.perf_counter()
            try:
                value = await call
                return value, SourceStatus(
                    state="ok", latency_ms=(time.perf_counter() - started) * 1000
                )
            except AuthorityFailure as exc:
                return None, SourceStatus(
                    state="error",
                    latency_ms=(time.perf_counter() - started) * 1000,
                    failure=exc.to_wire(),
                )

        (hub_page, hub_status), (mount_page, kernel_status) = await asyncio.gather(
            measured(
                self.hub.list_devices(
                    owner_id=owner_id,
                    authorization=hub_authorization,
                )
            ),
            measured(self.kernel.list_mounts(owner_id=owner_id)),
        )
        if hub_status.failure and hub_status.failure.kind in {
            "unauthorized",
            "forbidden",
        }:
            failure = hub_status.failure
            raise AuthorityFailure(
                "hub",
                failure.kind,
                failure.detail,
                401 if failure.kind == "unauthorized" else 403,
                failure.upstream_status,
            )
        return OwnerInventory(
            owner_id=owner_id,
            degraded=hub_page is None or mount_page is None,
            hub=hub_status,
            kernel=kernel_status,
            devices=hub_page.devices if hub_page else (),
            mounts=mount_page.mounts if mount_page else (),
        )

    @staticmethod
    def capabilities() -> BoundaryCapabilities:
        return BoundaryCapabilities(
            supported=(
                "data.companion-identity.read",
                "data.owner-workspace.initialize",
                "hub.device-admission.read-write",
                "kernel.device-mount.read-write",
                "admin.device-admission.workflow",
            ),
            unavailable_without_producer_contract=(
                "data.owner-management",
                "data.companion-management",
                "data.persona-genome-management",
                "data.memory-realm-catalog-management",
                "data.face-and-guard-management",
                "global-audit-projection",
                "runtime-presence-telemetry-projection",
            ),
        )
