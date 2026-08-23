"""Admin application orchestration across independent control authorities."""

from __future__ import annotations

import asyncio
import time
from uuid import UUID, uuid5

import httpx
from eidolon_sdk.biz.system_data import CompanionRuntimeSnapshot

from ..settings import Settings
from .clients import (
    DataAuthorityClient,
    DataWorkspaceAuthorityClient,
    HubManagementClient,
    KernelMountClient,
    MemoryRecollectionsClient,
)
from .contracts import (
    HubDevice,
    OwnerDeviceHistory,
    BoundaryCapabilities,
    ControllerDeviceAdmissionRequest,
    ControllerDeviceRemovalRequest,
    DeviceAdmissionRequest,
    DeviceAdmissionResult,
    DeviceRemovalResult,
    HubDevicePage,
    HubLifecycleStatus,
    KernelMount,
    KernelMountPage,
    OwnerInventory,
    SourceStatus,
    WorkspaceInitializeRequest,
    WorkspaceOperation,
    WorkflowStep,
)
from .directory import SystemDirectoryClient
from .errors import AuthorityFailure
from .hub_credentials import HubAdminCredentialIssuer


#: The Hub's scope for devices nobody holds yet. A device enrols into it and
#: leaves it when an Owner accepts the device, which is why both the pending
#: queue and the history have to look there as well as at the Owner.
UNCLAIMED_SCOPE = "unclaimed"

_CONTROLLER_ADMISSION_NAMESPACE = UUID("2f9f5cd8-ddb8-55b6-b4c2-13ff7cd64b3e")
_CONTROLLER_REMOVAL_NAMESPACE = UUID("6c0a1f42-9b3e-5d77-8a41-0d2b6f9e5c18")


def _child_request_id(workflow_id: str, suffix: str) -> str:
    return f"admin:{workflow_id}:{suffix}"


def _controller_admission_workflow_id(
    payload: ControllerDeviceAdmissionRequest,
) -> str:
    """Name this admission by everything the authorities key idempotency on.

    Downstream authorities do not take a request ID on trust: the Hub stores
    the last management request ID for a device beside a fingerprint of that
    mutation, and refuses an ID that comes back carrying a different one. Its
    fingerprint covers the requesting Controller and the owner being granted,
    so a workflow ID that leaves either out would hand the Hub one ID under two
    fingerprints — a conflict it is right to refuse and that no retry can
    clear. A household holds more than one phone, and every one of them derives
    the same mobile request ID for a given device on purpose, so the collision
    is reachable from the ordinary case of a second phone claiming a device.
    """

    operation = uuid5(
        _CONTROLLER_ADMISSION_NAMESPACE,
        "eidolon-controller-device-admission-v1:"
        f"{payload.device_id}:{payload.request_id}:"
        f"{payload.owner_id}:{payload.controller_id}",
    )
    return f"device-admission-{operation.hex}"


def _controller_removal_workflow_id(payload: ControllerDeviceRemovalRequest) -> str:
    """Same naming rule as admission; the Hub fingerprints a revocation too."""

    operation = uuid5(
        _CONTROLLER_REMOVAL_NAMESPACE,
        "eidolon-controller-device-removal-v1:"
        f"{payload.device_id}:{payload.request_id}:"
        f"{payload.owner_id}:{payload.controller_id}:{payload.reason}",
    )
    return f"device-removal-{operation.hex}"


class ControlPlaneService:
    def __init__(
        self,
        *,
        directory: SystemDirectoryClient,
        data: DataAuthorityClient,
        workspace: DataWorkspaceAuthorityClient,
        hub: HubManagementClient,
        kernel: KernelMountClient,
        memory: MemoryRecollectionsClient,
        hub_credentials: HubAdminCredentialIssuer | None = None,
    ) -> None:
        self.directory = directory
        self.data = data
        self.workspace = workspace
        self.hub = hub
        self.kernel = kernel
        self.memory = memory
        self.hub_credentials = hub_credentials

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
            memory=MemoryRecollectionsClient(
                discovery_url=settings.memory_discovery_url,
                client=http_client,
                timeout_seconds=settings.authority_timeout_seconds,
            ),
            hub_credentials=HubAdminCredentialIssuer(
                secret=settings.hub_management_jwt_secret.get_secret_value().encode(),
                ttl_seconds=settings.hub_management_jwt_ttl_seconds,
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

    async def get_owner_primary_runtime(
        self,
        owner_id: str,
    ) -> CompanionRuntimeSnapshot:
        return await self.data.get_owner_primary_runtime(owner_id)

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
        return await self._mount_approved_device(
            response_request_id=payload.request_id,
            owner_id=payload.owner_id,
            device_id=payload.device_id,
            companion_id=payload.companion_id,
            expected_mount_revision=payload.expected_mount_revision,
            replace_existing_mount=payload.replace_existing_mount,
            hub=hub,
            hub_step=WorkflowStep(
                name="hub_approval",
                state="committed",
                request_id=hub_request_id,
            ),
            mount_request_id=mount_request_id,
            attach_request_id=attach_request_id,
        )

    async def admit_controller_device(
        self,
        *,
        payload: ControllerDeviceAdmissionRequest,
    ) -> DeviceAdmissionResult:
        """Apply explicit Mobile approval, then mount and bind forward only.

        There is intentionally no rollback. Every downstream mutation uses a
        deterministic child request ID, so the same Mobile request resumes the
        last safe intermediate state after process or network interruption.
        """

        if self.hub_credentials is None:
            raise AuthorityFailure(
                "hub",
                "configuration",
                "Hub Owner credential issuer is unavailable",
                503,
            )
        workflow_id = _controller_admission_workflow_id(payload)
        hub_request_id = _child_request_id(workflow_id, "hub-approve")
        mount_request_id = _child_request_id(workflow_id, "kernel-mount")
        attach_request_id = _child_request_id(workflow_id, "kernel-attach")
        authorization = self.hub_credentials.issue(
            controller_id=payload.controller_id,
        )
        hub = await self.hub.approve(
            device_id=payload.device_id,
            owner_id=payload.owner_id,
            request_id=hub_request_id,
            authorization=authorization,
        )
        # A device the owner removed and is adding back still has its Kernel
        # mount record, inactive, at whatever revision the removal left. Mounting
        # it is a compare-and-swap against that revision, not against nothing.
        existing = await self._owner_mount(
            owner_id=payload.owner_id,
            device_id=payload.device_id,
            active_only=False,
        )
        return await self._mount_approved_device(
            response_request_id=payload.request_id,
            owner_id=payload.owner_id,
            device_id=payload.device_id,
            companion_id=payload.companion_id,
            expected_mount_revision=existing.revision if existing else 0,
            replace_existing_mount=False,
            mounted=existing if existing is not None and existing.active else None,
            hub=hub,
            hub_step=WorkflowStep(
                name="hub_approval",
                state="committed",
                request_id=hub_request_id,
            ),
            mount_request_id=mount_request_id,
            attach_request_id=attach_request_id,
        )

    async def remove_controller_device(
        self,
        *,
        payload: ControllerDeviceRemovalRequest,
    ) -> DeviceRemovalResult:
        """Withdraw a device's grant, then drop its mount.

        The order is the one that holds under interruption: once the Hub has
        revoked, the device can no longer obtain channel credentials, so a
        Kernel step that fails leaves something inert and listed rather than
        something invisible and live. Same forward-only, deterministic child
        request IDs as admission — retrying resumes, it does not duplicate.
        """

        if self.hub_credentials is None:
            raise AuthorityFailure(
                "hub",
                "configuration",
                "Hub Owner credential issuer is unavailable",
                503,
            )
        workflow_id = _controller_removal_workflow_id(payload)
        revoke_request_id = _child_request_id(workflow_id, "hub-revoke")
        unmount_request_id = _child_request_id(workflow_id, "kernel-unmount")
        authorization = self.hub_credentials.issue(controller_id=payload.controller_id)
        try:
            hub = await self.hub.revoke(
                device_id=payload.device_id,
                # The Owner this removal is carried out for. The Hub checks it
                # against its own record, so a request that arrived here with
                # mismatched parts is refused at the authority as well.
                owner_scope=payload.owner_id,
                reason=payload.reason,
                request_id=revoke_request_id,
                authorization=authorization,
            )
        except AuthorityFailure as exc:
            return DeviceRemovalResult(
                request_id=payload.request_id,
                outcome="retry_required" if exc.retryable else "blocked",
                completed_stage="received",
                recovery=(
                    "retry-forward-same-request-id"
                    if exc.retryable
                    else "operator-action-required"
                ),
                steps=(
                    WorkflowStep(
                        name="hub_revocation",
                        state="failed",
                        request_id=revoke_request_id,
                        failure=exc.to_wire(),
                    ),
                    WorkflowStep(name="kernel_unmount", state="not_attempted"),
                ),
            )
        steps = [
            WorkflowStep(
                name="hub_revocation",
                state="committed",
                request_id=revoke_request_id,
            )
        ]
        mount = await self._owner_mount(owner_id=payload.owner_id, device_id=payload.device_id)
        if mount is None:
            # Nothing to drop: the device was never mounted, or a previous
            # attempt already dropped it. Either way the end state is the one
            # asked for.
            steps.append(WorkflowStep(name="kernel_unmount", state="not_requested"))
            return DeviceRemovalResult(
                request_id=payload.request_id,
                outcome="completed",
                completed_stage="kernel_unmounted",
                steps=tuple(steps),
                hub=hub,
            )
        try:
            await self.kernel.unmount(
                owner_id=payload.owner_id,
                device_id=payload.device_id,
                request_id=unmount_request_id,
                expected_revision=mount.revision,
            )
        except AuthorityFailure as exc:
            steps.append(
                WorkflowStep(
                    name="kernel_unmount",
                    state="failed",
                    request_id=unmount_request_id,
                    revision=mount.revision,
                    failure=exc.to_wire(),
                )
            )
            return DeviceRemovalResult(
                request_id=payload.request_id,
                outcome="retry_required" if exc.retryable else "blocked",
                completed_stage="hub_revoked",
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
                name="kernel_unmount",
                state="committed",
                request_id=unmount_request_id,
                revision=mount.revision,
            )
        )
        return DeviceRemovalResult(
            request_id=payload.request_id,
            outcome="completed",
            completed_stage="kernel_unmounted",
            steps=tuple(steps),
            hub=hub,
        )

    async def _owner_mount(self, *, owner_id: str, device_id: str, active_only: bool = True):
        page = await self.kernel.list_mounts(owner_id=owner_id)
        for mount in page.mounts:
            if mount.device_id == device_id and (mount.active or not active_only):
                return mount
        return None

    async def list_pending_device_enrollments(
        self,
        *,
        controller_id: str,
    ) -> HubDevicePage:
        """Return Hub's screen-independent, unclaimed enrollment queue."""

        if self.hub_credentials is None:
            raise AuthorityFailure(
                "hub",
                "configuration",
                "Hub Admin credential issuer is unavailable",
                503,
            )
        return await self.hub.list_devices(
            owner_id=UNCLAIMED_SCOPE,
            authorization=self.hub_credentials.issue(controller_id=controller_id),
            lifecycle_state="pending-approval",
        )

    async def local_owner_inventory(
        self,
        *,
        owner_id: str,
        controller_id: str,
    ) -> OwnerInventory:
        """This Owner's devices, with a Hub credential Admin mints itself."""

        if self.hub_credentials is None:
            raise AuthorityFailure(
                "hub",
                "configuration",
                "Hub Admin credential issuer is unavailable",
                503,
            )
        return await self.inventory(
            owner_id=owner_id,
            hub_authorization=self.hub_credentials.issue(controller_id=controller_id),
        )

    async def local_owner_device_history(
        self,
        *,
        owner_id: str,
        controller_id: str,
        limit: int,
    ) -> OwnerDeviceHistory:
        """What has happened to this Owner's devices, in one answer.

        Four reads, and every one of them has to succeed. A history is read to
        find out whether something happened, so an unreachable half must not
        come back looking like a quiet stretch — the whole call fails, with
        the authority that failed still named in it.
        """

        if self.hub_credentials is None:
            raise AuthorityFailure(
                "hub",
                "configuration",
                "Hub Admin credential issuer is unavailable",
                503,
            )
        authorization = self.hub_credentials.issue(controller_id=controller_id)
        owned, unclaimed, directory, doorstep = await asyncio.gather(
            self.hub.latest_events(
                owner_id=owner_id,
                authorization=authorization,
                keep=limit,
            ),
            self.hub.latest_events(
                owner_id=UNCLAIMED_SCOPE,
                authorization=authorization,
                keep=limit,
            ),
            self.hub.list_devices(owner_id=owner_id, authorization=authorization),
            self.hub.list_devices(
                owner_id=UNCLAIMED_SCOPE,
                authorization=authorization,
            ),
        )
        # Newest first, and only as many as were asked for. The two scopes
        # interleave in time; the stream position that orders each one says
        # nothing about the other.
        events = sorted(
            (*owned, *unclaimed),
            key=lambda event: (event.occurred_at, event.stream_position),
            reverse=True,
        )[:limit]
        named = {entry.device_id: entry for entry in (*directory.devices, *doorstep.devices)}
        return OwnerDeviceHistory(
            owner_id=owner_id,
            events=tuple(events),
            # Only the devices these events are about. The rest of the
            # directory is another question, already answered elsewhere.
            devices=tuple(
                entry
                for device_id, entry in named.items()
                if any(event.device_id == device_id for event in events)
            ),
        )

    async def rename_owner_device(
        self,
        *,
        owner_id: str,
        controller_id: str,
        device_id: str,
        display_name: str,
    ) -> HubDevice:
        """Set what one of this Owner's devices is called."""

        if self.hub_credentials is None:
            raise AuthorityFailure(
                "hub",
                "configuration",
                "Hub Owner credential issuer is unavailable",
                503,
            )
        return await self.hub.rename(
            device_id=device_id,
            owner_scope=owner_id,
            display_name=display_name,
            authorization=self.hub_credentials.issue(controller_id=controller_id),
        )

    async def _mount_approved_device(
        self,
        *,
        response_request_id: str,
        owner_id: str,
        device_id: str,
        companion_id: str | None,
        expected_mount_revision: int,
        replace_existing_mount: bool,
        hub: HubLifecycleStatus,
        hub_step: WorkflowStep,
        mount_request_id: str,
        attach_request_id: str,
        mounted: KernelMount | None = None,
    ) -> DeviceAdmissionResult:
        steps = [
            hub_step,
        ]
        if mounted is not None:
            # Already where this step is trying to get to. Asking the Kernel to
            # mount it again is not idempotent — the same device cannot be
            # mounted over while it is active — so the state, not a repeated
            # request, is what makes admission safe to run on every connect.
            return await self._attach_mounted_device(
                response_request_id=response_request_id,
                owner_id=owner_id,
                device_id=device_id,
                companion_id=companion_id,
                hub=hub,
                steps=[*steps, WorkflowStep(name="kernel_mount", state="replayed", revision=mounted.revision)],
                mount=mounted,
                attach_request_id=attach_request_id,
            )
        try:
            mount_result = await self.kernel.mount(
                owner_id=owner_id,
                device_id=device_id,
                request_id=mount_request_id,
                expected_revision=expected_mount_revision,
                replace_existing=replace_existing_mount,
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
                        if companion_id
                        else "not_requested",
                        request_id=attach_request_id if companion_id else None,
                    ),
                )
            )
            return DeviceAdmissionResult(
                request_id=response_request_id,
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
        return await self._attach_mounted_device(
            response_request_id=response_request_id,
            owner_id=owner_id,
            device_id=device_id,
            companion_id=companion_id,
            hub=hub,
            steps=steps,
            mount=mount_result.mount,
            attach_request_id=attach_request_id,
        )

    async def _attach_mounted_device(
        self,
        *,
        response_request_id: str,
        owner_id: str,
        device_id: str,
        companion_id: str | None,
        hub: HubLifecycleStatus,
        steps: list[WorkflowStep],
        mount: KernelMount,
        attach_request_id: str,
    ) -> DeviceAdmissionResult:
        if companion_id is None:
            steps.append(
                WorkflowStep(name="companion_attachment", state="not_requested")
            )
            return DeviceAdmissionResult(
                request_id=response_request_id,
                outcome="completed",
                completed_stage="kernel_mounted",
                steps=tuple(steps),
                hub=hub,
                mount=mount,
            )
        if mount.attached_companion_id == companion_id:
            steps.append(
                WorkflowStep(
                    name="companion_attachment",
                    state="replayed",
                    revision=mount.revision,
                )
            )
            return DeviceAdmissionResult(
                request_id=response_request_id,
                outcome="completed",
                completed_stage="companion_attached",
                steps=tuple(steps),
                hub=hub,
                mount=mount,
            )
        try:
            attach_result = await self.kernel.attach(
                owner_id=owner_id,
                device_id=device_id,
                companion_id=companion_id,
                request_id=attach_request_id,
                expected_revision=mount.revision,
            )
        except AuthorityFailure as exc:
            steps.append(
                WorkflowStep(
                    name="companion_attachment",
                    state="failed",
                    request_id=attach_request_id,
                    revision=mount.revision,
                    failure=exc.to_wire(),
                )
            )
            return DeviceAdmissionResult(
                request_id=response_request_id,
                outcome="retry_required" if exc.retryable else "blocked",
                completed_stage="kernel_mounted",
                recovery=(
                    "retry-forward-same-request-id"
                    if exc.retryable
                    else "operator-action-required"
                ),
                steps=tuple(steps),
                hub=hub,
                mount=mount,
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
            request_id=response_request_id,
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

    async def list_owner_device_mounts(self, owner_id: str) -> KernelMountPage:
        """Return Kernel-owned membership without requiring Hub operator authority.

        This narrow read is consumed by the Controller-authenticated Local API.
        It deliberately excludes pending Hub enrollments and directory metadata:
        those require the separate Device admission authority contract.
        """

        return await self.kernel.list_mounts(owner_id=owner_id)

    @staticmethod
    def capabilities() -> BoundaryCapabilities:
        return BoundaryCapabilities(
            supported=(
                "data.companion-identity.read",
                "data.owner-primary-runtime.read",
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
