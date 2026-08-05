"""Stable host-bootstrap facts.

Owner, Companion, external Device admission, and Kernel Mount deliberately do
not appear as mutable entities here. Bootstrap owns only Host commissioning,
Controller grants, network operations, and their reset-epoch boundary.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


class ClaimState(StrEnum):
    UNCLAIMED = "unclaimed"
    CLAIMED = "claimed"


class NetworkState(StrEnum):
    UNCONFIGURED = "unconfigured"
    STAGING = "staging"
    CONNECTED = "connected"
    DEGRADED = "degraded"
    ROLLING_BACK = "rolling_back"


class WorkspaceState(StrEnum):
    ABSENT = "absent"
    PROVISIONING = "provisioning"
    READY = "ready"
    DEGRADED = "degraded"


class RecoveryState(StrEnum):
    NORMAL = "normal"
    PHYSICALLY_ARMED = "physically_armed"
    CONTROLLER_RECOVERY = "controller_recovery"
    FACTORY_RESET_PENDING = "factory_reset_pending"


class ControllerRole(StrEnum):
    HOST_ADMIN = "host_admin"


class BootstrapOperationType(StrEnum):
    INITIAL_NETWORK = "initial_network"
    CHANGE_NETWORK = "change_network"


class BootstrapOperationState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_CONFIRMATION = "waiting_confirmation"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    COMPENSATING = "compensating"


@dataclass(frozen=True, slots=True)
class HostIdentity:
    host_id: str
    public_key: str
    public_key_fingerprint: str


@dataclass(frozen=True, slots=True)
class BootstrapState:
    reset_epoch: int
    claim_state: ClaimState
    network_state: NetworkState
    workspace_state: WorkspaceState
    recovery_state: RecoveryState
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result.update(
            claim_state=self.claim_state.value,
            network_state=self.network_state.value,
            workspace_state=self.workspace_state.value,
            recovery_state=self.recovery_state.value,
        )
        return result


@dataclass(frozen=True, slots=True)
class CommissioningSessionMetadata:
    session_id: str
    created_at: str
    expires_at: str
    consumed_at: str | None = None
    revoked_at: str | None = None

    def to_dict(self) -> dict[str, str | bool | None]:
        return {
            "session_id": self.session_id,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "consumed_at": self.consumed_at,
            "revoked_at": self.revoked_at,
        }


@dataclass(frozen=True, slots=True)
class ControllerGrant:
    controller_id: str
    public_key: str
    public_key_fingerprint: str
    role: ControllerRole
    display_name: str
    platform: str
    reset_epoch: int
    created_at: str
    revoked_at: str | None = None

    def to_dict(self) -> dict[str, str | int | None]:
        return {
            "controller_id": self.controller_id,
            "public_key": self.public_key,
            "public_key_fingerprint": self.public_key_fingerprint,
            "role": self.role.value,
            "display_name": self.display_name,
            "platform": self.platform,
            "reset_epoch": self.reset_epoch,
            "created_at": self.created_at,
            "revoked_at": self.revoked_at,
        }


@dataclass(frozen=True, slots=True)
class BootstrapOperation:
    operation_id: str
    operation_type: BootstrapOperationType
    state: BootstrapOperationState
    target: str
    reset_epoch: int
    created_at: str
    updated_at: str
    error_code: str | None = None

    def to_dict(self) -> dict[str, str | int | None]:
        return {
            "operation_id": self.operation_id,
            "operation_type": self.operation_type.value,
            "state": self.state.value,
            "target": self.target,
            "reset_epoch": self.reset_epoch,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "error_code": self.error_code,
        }
