"""Durable authority required by Bootstrap, independent of storage format."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..domain import (
    BootstrapOperation,
    BootstrapOperationState,
    BootstrapState,
    CommissioningSessionMetadata,
    CommissioningSessionSeed,
    ControllerGrant,
    NetworkState,
)


class BootstrapStateConflict(RuntimeError):
    """A durable authority mutation conflicts with current Bootstrap state."""


MAX_COMMISSIONING_FAILED_ATTEMPTS = 5


@runtime_checkable
class BootstrapStateStore(Protocol):
    """The minimal state that must survive daemon and host restarts.

    Diagnostics and daemon lifecycle logs deliberately do not belong here.
    systemd/journald owns those records.
    """

    def open(self) -> None: ...

    def initialize(self, now: str) -> None: ...

    def get_state(self) -> BootstrapState: ...

    def issue_commissioning_session(
        self,
        *,
        session_id: str,
        secret_hash: str,
        created_at: str,
        expires_at: str,
    ) -> None: ...

    def latest_commissioning_session(
        self,
    ) -> CommissioningSessionMetadata | None: ...

    def authorize_commissioning_session(
        self,
        *,
        session_id: str,
        secret_hash: str,
        now: str,
    ) -> CommissioningSessionMetadata: ...

    def claim_controller(
        self,
        *,
        session_id: str,
        secret_hash: str,
        grant: ControllerGrant,
        now: str,
    ) -> ControllerGrant: ...

    def get_controller(self, controller_id: str) -> ControllerGrant | None:
        """This Controller's Grant in the Host's current reset epoch, if any.

        Scoped to the epoch because a Grant from a retired one is history: it
        must stay readable through ``list_controllers`` and must never again
        answer "who holds this Host".
        """
        ...

    def list_controllers(self) -> list[ControllerGrant]: ...

    def revoke_controller(
        self, *, controller_id: str, now: str
    ) -> ControllerGrant: ...

    def bind_controller_owner(
        self,
        *,
        controller_id: str,
        owner_id: str,
        reset_epoch: int,
        now: str,
    ) -> ControllerGrant: ...

    def create_operation(self, operation: BootstrapOperation) -> BootstrapOperation: ...

    def get_operation(self, operation_id: str) -> BootstrapOperation | None: ...

    def update_operation(
        self,
        operation_id: str,
        *,
        state: BootstrapOperationState,
        network_state: NetworkState,
        updated_at: str,
        error_code: str | None = None,
    ) -> BootstrapOperation: ...

    def fail_interrupted_operations(self, *, now: str) -> int: ...

    def reconcile_network_state(
        self,
        *,
        network_state: NetworkState,
        now: str,
    ) -> None: ...

    def reset_authority(
        self,
        *,
        network_state: NetworkState,
        now: str,
        recovery_session: CommissioningSessionSeed | None,
    ) -> BootstrapState:
        """Advance one authority epoch, optionally opening the way back.

        ``recovery_session`` is applied in the same transaction as the
        revocation, so the Host cannot come to rest with nobody holding it and
        no window through which anyone could.
        """
        ...

    def close(self) -> None: ...
