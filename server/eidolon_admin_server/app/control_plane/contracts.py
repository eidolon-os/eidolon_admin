"""Strict consumed contracts and Admin-owned control-plane read models."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from eidolon_sdk.device_foundation.v1 import (
    DeviceRef,
    RevokeClaimResult as HubClaimRevocationResult,
)


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


class CompanionFace(StrictModel):
    """Whether this Companion has a face, and which one — never the face."""

    operation: Literal["companion.face"]
    companion_id: str = Field(min_length=1, max_length=64)
    has_face: bool
    face_asset_id: str | None = None
    sha256: str | None = None
    size_bytes: int | None = None
    updated_at: str | None = None


class OwnerRecollections(StrictModel):
    """What an Owner's Eidolon holds about a question.

    The records themselves are passed through as memory shaped them rather
    than re-modelled here: Admin is projecting a boundary, not deciding what a
    memory is.
    """

    operation: Literal["owner.recollections"] = "owner.recollections"
    owner_id: str = Field(min_length=1, max_length=64)
    query: str
    recollections: list[dict[str, Any]]


class OwnerIdentity(StrictModel):
    operation: Literal["owner.identity"]
    owner_id: str = Field(min_length=1, max_length=64)
    #: What this person is called. Given at first use; correctable since.
    display_name: str = Field(default="", max_length=128)
    lifecycle_state: Literal["active", "inactive"]


class OwnerRenameRequest(StrictModel):
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
    device_ref: DeviceRef | None = None


class HubDevicePage(StrictModel):
    operation: Literal["device.directory-page"]
    next_cursor: str | None = Field(default=None, max_length=128)
    devices: tuple[HubDevice, ...] = Field(default=(), max_length=100)

    @field_validator("devices", mode="before")
    @classmethod
    def _array(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value


class HubDeviceEvent(StrictModel):
    """One thing the Hub recorded happening to a device."""

    operation: Literal["device.management-event"]
    stream_position: int = Field(ge=1)
    event_id: str = Field(min_length=1, max_length=255)
    event_type: str = Field(min_length=1, max_length=255)
    source: str = Field(min_length=1, max_length=512)
    principal_id: str = Field(min_length=1, max_length=255)
    device_id: str = Field(min_length=1, max_length=255)
    occurred_at: datetime
    data: dict[str, Any] = Field(default_factory=dict)


class HubDeviceEventPage(StrictModel):
    operation: Literal["device.management-event-page"]
    next_stream_position: int = Field(ge=0)
    events: tuple[HubDeviceEvent, ...] = Field(default=(), max_length=500)

    @field_validator("events", mode="before")
    @classmethod
    def _array(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value


class OwnerDeviceHistory(StrictModel):
    """What has happened to this Owner's devices, and what those devices are called.

    Two scopes, not one. A device enrols before anyone holds it, so the Hub
    files that moment under `unclaimed`; only the approval that follows is
    filed under the Owner. Answering from the Owner's scope alone would show
    an Eidolon accepting devices that had never knocked — which is exactly the
    half of the story someone watching a device arrive is waiting for.

    The directory travels with the events because an event names a device by
    identifier and a person does not know their devices by identifier.
    """

    operation: Literal["admin.owner-device-history"] = "admin.owner-device-history"
    owner_id: str = Field(min_length=1, max_length=64)
    events: tuple[HubDeviceEvent, ...] = Field(default=(), max_length=500)
    devices: tuple[HubDevice, ...] = Field(default=(), max_length=200)


class HubLifecycleStatus(StrictModel):
    operation: Literal["device.lifecycle-status"]
    device_id: str = Field(min_length=1, max_length=128)
    owner_id: str | None = Field(default=None, max_length=64)
    lifecycle_state: Literal["pending-approval", "approved", "revoked"]


class HubDeviceControlOperationStatus(StrictModel):
    operation: Literal["device-control.operation-status"]
    event_id: str = Field(min_length=3, max_length=128)
    operation_id: str = Field(min_length=3, max_length=255)
    operation_type: Literal["channel.device-access.revoke"]
    device_ref: DeviceRef
    state: Literal["pending", "delivered"]
    attempt_count: int = Field(ge=0)
    next_attempt_at: datetime
    delivered_at: datetime | None = None
    last_error: str = Field(default="", max_length=512)


class KernelMount(StrictModel):
    operation: Literal["kernel.device-mount"]
    device_id: str = Field(min_length=1, max_length=128)
    owner_id: str = Field(min_length=1, max_length=64)
    device_ref: DeviceRef
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
    """A durable intent result plus observations from independent authorities."""

    operation: Literal["admin.device-removal-workflow"] = "admin.device-removal-workflow"
    request_id: str
    intent_id: str = Field(min_length=1, max_length=128)
    device_ref: DeviceRef
    outcome: Literal["completed", "accepted", "blocked"]
    completed_stage: Literal["received", "claim_revoked", "converged"]
    distributed_atomic: Literal[False] = False
    compensation: Literal["none-safe-intermediate"] = "none-safe-intermediate"
    recovery: Literal["none", "retry-forward-same-request-id", "operator-action-required"] = "none"
    steps: tuple[WorkflowStep, ...]
    hub: HubClaimRevocationResult | None = None
    conditions: tuple["RemovalCondition", ...] = ()


class RemovalCondition(StrictModel):
    name: Literal[
        "platform_access_revoked",
        "mount_removed",
        "channel_access_revoked",
        "device_erase_acknowledged",
    ]
    state: Literal["true", "false", "unknown"]
    authority: Literal["hub", "kernel", "device-control"]
    authority_ref: str | None = Field(default=None, max_length=255)
    observed_at: datetime


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
