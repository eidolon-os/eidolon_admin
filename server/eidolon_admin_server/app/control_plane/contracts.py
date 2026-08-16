"""Strict consumed contracts and Admin-owned control-plane read models."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ServiceEndpoint(StrictModel):
    operation: Literal["system.service-endpoint"]
    service_id: str = Field(min_length=1, max_length=128)
    endpoint_id: str = Field(min_length=1, max_length=128)
    protocol: str = Field(min_length=1, max_length=32)
    address: str = Field(min_length=1, max_length=2048)
    contract: str = Field(min_length=1, max_length=256)


class CompanionIdentity(StrictModel):
    operation: Literal["companion.identity"]
    companion_id: str = Field(min_length=1, max_length=64)
    owner_id: str = Field(min_length=1, max_length=64)
    #: What the Owner calls this Eidolon. Defaulted so a Host whose Data
    #: predates answering with it still parses.
    display_name: str = Field(default="", max_length=128)
    lifecycle_state: Literal["active", "inactive"]


class PersonaChapter(StrictModel):
    genome_id: str = Field(min_length=1, max_length=64)
    version: int = Field(ge=1)
    lifecycle_state: Literal["committed", "proposed", "rejected", "stale"]
    change_summary: str = Field(default="", max_length=4096)
    restored_from_version: int | None = None
    is_current: bool = False
    created_at: str


class PersonaTimeline(StrictModel):
    operation: Literal["companion.persona-timeline"]
    companion_id: str = Field(min_length=1, max_length=64)
    chapters: tuple[PersonaChapter, ...] = ()


class PersonaRestoreRequest(StrictModel):
    genome_id: str = Field(min_length=1, max_length=64)
    change_summary: str = Field(default="", max_length=4096)


class DeviceRenameCommand(StrictModel):
    display_name: str = Field(min_length=1, max_length=128)


class CompanionRenameRequest(StrictModel):
    display_name: str = Field(min_length=1, max_length=128)


class WorkspaceInitializeRequest(StrictModel):
    owner_display_name: str = Field(min_length=1, max_length=128)
    companion_display_name: str = Field(default="Eidolon", min_length=1, max_length=128)


class WorkspaceOwner(StrictModel):
    owner_id: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=128)
    lifecycle_state: Literal["active"]


class WorkspaceResources(StrictModel):
    state: Literal["ready"]
    primary_companion_id: str = Field(min_length=1, max_length=64)
    persona_genome_id: str = Field(min_length=1, max_length=64)
    memory_realm_id: str = Field(min_length=1, max_length=64)


class WorkspaceOperation(StrictModel):
    contract_version: Literal["1"]
    operation: Literal["owner-workspace.initialize"]
    operation_id: str = Field(
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    )
    request_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    status: Literal["succeeded"]
    owner: WorkspaceOwner
    workspace: WorkspaceResources


class HubPropertyAffordance(StrictModel):
    name: str = Field(min_length=1, max_length=128)
    schema_: dict[str, Any] = Field(alias="schema")
    observable: bool
    writable: bool


class HubActionAffordance(StrictModel):
    name: str = Field(min_length=1, max_length=128)
    version: int = Field(ge=1, le=65_535)
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    idempotent: bool


class HubEventAffordance(StrictModel):
    name: str = Field(min_length=1, max_length=128)
    data_schema: dict[str, Any]


class HubMediaCapability(StrictModel):
    kind: Literal["audio", "video"]
    direction: Literal["publish", "subscribe", "bidirectional"]
    codecs: tuple[str, ...] = Field(max_length=32)


class HubDeviceManifest(StrictModel):
    schema_version: Literal[1]
    title: str = Field(min_length=1, max_length=128)
    properties: tuple[HubPropertyAffordance, ...] = Field(max_length=64)
    actions: tuple[HubActionAffordance, ...] = Field(max_length=64)
    events: tuple[HubEventAffordance, ...] = Field(max_length=64)
    media: tuple[HubMediaCapability, ...] = Field(max_length=16)


class HubDevice(StrictModel):
    operation: Literal["device.directory-entry"]
    device_id: str = Field(min_length=1, max_length=128)
    owner_scope: str = Field(min_length=1, max_length=64)
    display_name: str = Field(max_length=128)
    device_kind: str = Field(min_length=1, max_length=96)
    manifest: HubDeviceManifest
    manifest_revision: str = Field(min_length=1, max_length=128)
    lifecycle_state: Literal["pending-approval", "approved", "revoked"]
    enrolled_at: datetime
    updated_at: datetime


class HubDevicePage(StrictModel):
    operation: Literal["device.directory-page"]
    next_cursor: str | None = Field(default=None, max_length=128)
    devices: tuple[HubDevice, ...] = Field(default=(), max_length=100)

    @field_validator("devices", mode="before")
    @classmethod
    def _array(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value


class HubLifecycleStatus(StrictModel):
    operation: Literal["device.lifecycle-status"]
    device_id: str = Field(min_length=1, max_length=128)
    owner_id: str | None = Field(default=None, max_length=64)
    lifecycle_state: Literal["pending-approval", "approved", "revoked"]


class KernelMount(StrictModel):
    operation: Literal["kernel.device-mount"]
    device_id: str = Field(min_length=1, max_length=128)
    owner_id: str = Field(min_length=1, max_length=64)
    attached_companion_id: str | None = Field(default=None, min_length=1, max_length=64)
    revision: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime
    request_id: str = Field(min_length=1, max_length=96)
    fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    active: bool


class KernelMountPage(StrictModel):
    operation: Literal["kernel.device-mount-page"]
    next_cursor: str | None = Field(default=None, max_length=128)
    mounts: tuple[KernelMount, ...] = Field(default=(), max_length=100)

    @field_validator("mounts", mode="before")
    @classmethod
    def _array(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value


class KernelMutationResult(StrictModel):
    operation: Literal["kernel.device-mount-mutation-result"]
    mount: KernelMount
    audit_position: int = Field(ge=1)
    replayed: bool


class DeviceAdmissionRequest(StrictModel):
    request_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._:-]+$")
    owner_id: str = Field(min_length=1, max_length=64)
    device_id: str = Field(min_length=1, max_length=128)
    companion_id: str | None = Field(default=None, min_length=1, max_length=64)
    expected_mount_revision: int = Field(default=0, ge=0, strict=True)
    replace_existing_mount: bool = False


class ControllerDeviceAdmissionRequest(StrictModel):
    """Internal service input derived from explicit Mobile confirmation."""

    contract_version: Literal["1"]
    request_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    owner_id: str = Field(min_length=1, max_length=64)
    controller_id: str = Field(pattern=r"^ectrl-[0-9a-f]{20}$")
    device_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    companion_id: str | None = Field(default=None, min_length=1, max_length=64)


class ControllerDeviceRemovalRequest(StrictModel):
    """Internal service input derived from explicit Controller confirmation."""

    contract_version: Literal["1"]
    request_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    owner_id: str = Field(min_length=1, max_length=64)
    controller_id: str = Field(pattern=r"^ectrl-[0-9a-f]{20}$")
    device_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    reason: str = Field(default="owner-removed", min_length=1, max_length=128)


class WorkflowFailure(StrictModel):
    authority: Literal["directory", "data", "hub", "kernel"]
    kind: Literal[
        "unauthorized",
        "forbidden",
        "not_found",
        "conflict",
        "invalid_request",
        "unavailable",
        "upstream_failure",
        "contract_violation",
        "configuration",
    ]
    detail: str
    upstream_status: int | None = None
    retryable: bool


class WorkflowStep(StrictModel):
    name: Literal[
        "hub_approval",
        "kernel_mount",
        "companion_attachment",
        "hub_revocation",
        "kernel_unmount",
    ]
    state: Literal["committed", "replayed", "failed", "not_requested", "not_attempted"]
    request_id: str | None = None
    revision: int | None = None
    failure: WorkflowFailure | None = None


class DeviceAdmissionResult(StrictModel):
    operation: Literal["admin.device-admission-workflow"] = (
        "admin.device-admission-workflow"
    )
    request_id: str
    outcome: Literal["completed", "retry_required", "blocked"]
    completed_stage: Literal[
        "received", "hub_approved", "kernel_mounted", "companion_attached"
    ]
    distributed_atomic: Literal[False] = False
    compensation: Literal["none-safe-intermediate"] = "none-safe-intermediate"
    recovery: Literal[
        "none", "retry-forward-same-request-id", "operator-action-required"
    ] = "none"
    steps: tuple[WorkflowStep, ...]
    hub: HubLifecycleStatus | None = None
    mount: KernelMount | None = None


class DeviceRemovalResult(StrictModel):
    """What removing a device actually accomplished, stage by stage.

    Removal is the reverse of admission and just as distributed: the grant is
    withdrawn at the Hub, then the mount is dropped at the Kernel. The Hub step
    is the one that matters — after it the device cannot obtain credentials —
    so a Kernel step that fails leaves a device that is off but still listed,
    and says so rather than reporting success.
    """

    operation: Literal["admin.device-removal-workflow"] = "admin.device-removal-workflow"
    request_id: str
    outcome: Literal["completed", "retry_required", "blocked"]
    completed_stage: Literal["received", "hub_revoked", "kernel_unmounted"]
    distributed_atomic: Literal[False] = False
    compensation: Literal["none-safe-intermediate"] = "none-safe-intermediate"
    recovery: Literal["none", "retry-forward-same-request-id", "operator-action-required"] = "none"
    steps: tuple[WorkflowStep, ...]
    hub: HubLifecycleStatus | None = None


class SourceStatus(StrictModel):
    state: Literal["ok", "error"]
    latency_ms: float = Field(ge=0)
    failure: WorkflowFailure | None = None


class OwnerInventory(StrictModel):
    operation: Literal["admin.owner-device-inventory"] = "admin.owner-device-inventory"
    owner_id: str
    degraded: bool
    hub: SourceStatus
    kernel: SourceStatus
    devices: tuple[HubDevice, ...]
    mounts: tuple[KernelMount, ...]


class BoundaryCapabilities(StrictModel):
    operation: Literal["admin.control-plane-capabilities"] = (
        "admin.control-plane-capabilities"
    )
    supported: tuple[str, ...]
    unavailable_without_producer_contract: tuple[str, ...]
    global_audit_projection_configured: Literal[False] = False
    admin_sqlite_authority: Literal[False] = False
