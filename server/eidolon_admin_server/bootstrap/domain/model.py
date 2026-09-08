"""Stable host-bootstrap facts.

Owner, Companion, external Device admission, and Kernel Mount deliberately do
not appear as mutable entities here. Bootstrap owns only Host commissioning,
Controller grants, network operations, and their reset-epoch boundary.
"""

from __future__ import annotations

from datetime import datetime, timedelta

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
    """What this Host knows about its own authority, and nothing else.

    It used to also carry ``owner_id`` and ``workspace_state`` — a record that
    the Data plane held a Workspace for this Host. Neither was a fact of
    Bootstrap's: ``owner_id`` is ``owner_<uuid5(host_id).hex>``, so it named
    nothing this Host did not already know, and whether that Workspace exists
    is Data's to answer. Stored here it was a durable claim about another
    plane's store, and a durable claim can outlive what it describes — one
    Host kept asserting a Workspace a data reset had destroyed, so every phone
    ever claimed onto it inherited that Owner scope and was refused at setup,
    identically and forever, with no operation offered that could clear it.

    Two stores holding one fact is what made that reachable, so now one does.
    Owner scope reaches a request from the Controller session that resolved it
    against Data; that memo expires with the session and dies with the
    process, and a memo which cannot outlive a session cannot strand a Host.
    """

    reset_epoch: int
    claim_state: ClaimState
    network_state: NetworkState
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result.update(
            claim_state=self.claim_state.value,
            network_state=self.network_state.value,
        )
        return result


def lockout_until(now: str, *, seconds: int) -> str:
    """When a window that has just locked starts accepting codes again.

    Takes and returns the same ISO-8601 `...Z` shape the stores keep, so the
    comparison that reads it stays a string comparison like every other one
    beside it — a lock expressed in a different format is a lock that sorts
    wrong on the day someone compares them directly.
    """

    moment = datetime.fromisoformat(now.replace("Z", "+00:00"))
    return (moment + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class CommissioningSessionMetadata:
    """One setup window.

    ``expires_at`` is None for a window with no clock on it, which is every
    window this Host opens now: the only thing that closes one is being
    consumed by a claim, or being superseded when a newer one is minted
    (ADR-0007). A device sits in its box for a month, and a person who powers
    it on and walks off to make tea comes back to a window still open — neither
    of which a timer can be set for.

    ``locked_until`` is what replaced revoking on the fifth wrong code. Revoking
    an unexpiring window means nobody can reopen it, which for the code printed
    on the chassis is a device bricked out of the box; a lock lets the guesses
    cost time instead.
    """

    session_id: str
    created_at: str
    expires_at: str | None = None
    consumed_at: str | None = None
    revoked_at: str | None = None
    failed_attempts: int = 0
    locked_until: str | None = None

    def to_dict(self) -> dict[str, str | int | None]:
        return {
            "session_id": self.session_id,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "consumed_at": self.consumed_at,
            "revoked_at": self.revoked_at,
            "failed_attempts": self.failed_attempts,
            "locked_until": self.locked_until,
        }

    def is_open(self, now: str) -> bool:
        """Whether a code submitted at ``now`` could still be accepted."""

        if self.consumed_at is not None or self.revoked_at is not None:
            return False
        if self.expires_at is not None and self.expires_at <= now:
            return False
        return self.locked_until is None or self.locked_until <= now


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
    expires_at: str | None = None


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
