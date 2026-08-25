"""Stable host-bootstrap facts.

Owner, Companion, external Device admission, and Kernel Mount deliberately do
not appear as mutable entities here. Bootstrap owns only Host commissioning,
Controller grants, network operations, and their reset-epoch boundary.
"""

from __future__ import annotations

import re
import secrets
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
    """Whether this Host has an Owner's workspace on it yet.

    Provisioning and degraded were modelled and never written. A value nothing
    can produce is not a state a reader has to handle; it is a promise the
    screen makes on the Host's behalf.
    """

    ABSENT = "absent"
    READY = "ready"


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
    owner_id: str | None
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        # Owner scope is authority state, not part of the public bootstrap state.
        result.pop("owner_id")
        result.update(
            claim_state=self.claim_state.value,
            network_state=self.network_state.value,
            workspace_state=self.workspace_state.value,
        )
        return result


@dataclass(frozen=True, slots=True)
class CommissioningSessionMetadata:
    session_id: str
    created_at: str
    expires_at: str
    consumed_at: str | None = None
    revoked_at: str | None = None
    failed_attempts: int = 0

    def to_dict(self) -> dict[str, str | int | None]:
        return {
            "session_id": self.session_id,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "consumed_at": self.consumed_at,
            "revoked_at": self.revoked_at,
            "failed_attempts": self.failed_attempts,
        }


@dataclass(frozen=True, slots=True)
class CommissioningSessionSeed:
    """Everything needed to open one setup window, minus the secret itself.

    Passed into ``reset_authority`` rather than issued beside it, because
    withdrawing authority and opening the way back are one intention. Split
    into two store calls, the Host had a reachable state between them with no
    Controller and no window: nobody could manage it, no phone could claim it,
    and leaving required a second operator command nothing had asked for.
    """

    session_id: str
    secret_hash: str
    expires_at: str


@dataclass(frozen=True, slots=True)
class ControllerGrant:
    """One phone's authority over this Host, inside one reset epoch.

    ``(controller_id, reset_epoch)`` is the identity, not ``controller_id``.
    The tables used to enforce the second, which silently redefined "this
    phone has been authorized once, ever" as "this phone may never be
    authorized again" — so the one phone an Owner actually holds was the one
    that could not come back after a recovery.
    """

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


#: A Setup code is read aloud or typed from a label, so it is short by
#: necessity. Eight digits is what Matter and HomeKit both settled on: about
#: 26.6 bits, low enough to be usable and safe only because the session it
#: unlocks is one-time, expires, and dies after a few wrong guesses.
SETUP_CODE_DIGITS = 8
_SETUP_CODE = re.compile(r"^[0-9]{8}$")


def is_usable_setup_code(value: str) -> bool:
    """Whether a Setup code is well formed and not one a person would guess.

    Matter and HomeKit both refuse the same shapes: every digit the same, and
    the plain run up or down. They carry no less entropy than any other code,
    but they are what someone tries first, and they are what a factory prints
    by accident.
    """

    if _SETUP_CODE.fullmatch(value) is None:
        return False
    if len(set(value)) == 1:
        return False
    ascending = "".join(str(digit % 10) for digit in range(SETUP_CODE_DIGITS))
    return value not in {ascending, ascending[::-1]}


def generate_setup_code() -> str:
    """Draw a Setup code the Host has never used before, uniformly."""

    while True:
        candidate = f"{secrets.randbelow(10**SETUP_CODE_DIGITS):0{SETUP_CODE_DIGITS}d}"
        if is_usable_setup_code(candidate):
            return candidate
