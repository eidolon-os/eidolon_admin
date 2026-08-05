"""Durable authority required by Bootstrap, independent of storage format."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..domain import (
    BootstrapOperation,
    BootstrapOperationState,
    BootstrapState,
    CommissioningSessionMetadata,
    ControllerGrant,
    NetworkState,
)


class BootstrapStateConflict(RuntimeError):
    """A durable authority mutation conflicts with current Bootstrap state."""


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

    def get_controller(self, controller_id: str) -> ControllerGrant | None: ...

    def list_controllers(self) -> list[ControllerGrant]: ...

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

    def close(self) -> None: ...
