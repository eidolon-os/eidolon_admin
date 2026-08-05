"""Stable host-bootstrap facts.

Owner, Companion, external Device admission, and Kernel Mount deliberately do
not appear as mutable entities here. Bootstrap only stores an eventual stable
Owner reference in Controller grants, which will be introduced with auth.
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
