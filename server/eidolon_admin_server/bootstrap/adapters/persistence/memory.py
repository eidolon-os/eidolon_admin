"""In-memory state adapter for application tests and local simulations."""

from __future__ import annotations

import hmac
from dataclasses import replace

from ...domain import (
    BootstrapState,
    BootstrapOperation,
    BootstrapOperationState,
    ClaimState,
    CommissioningSessionMetadata,
    ControllerGrant,
    NetworkState,
    WorkspaceState,
)
from ...ports.state_store import (
    MAX_COMMISSIONING_FAILED_ATTEMPTS,
    BootstrapStateConflict,
)


class InMemoryBootstrapStateStore:
    """A non-durable adapter; never select it for a product service unit."""

    def __init__(self) -> None:
        self._opened = False
        self._state: BootstrapState | None = None
        self._sessions: list[tuple[CommissioningSessionMetadata, str]] = []
        self._session_controller_ids: dict[str, str] = {}
        self._controllers: dict[str, ControllerGrant] = {}
        self._operations: dict[str, BootstrapOperation] = {}

    def _require_open(self) -> None:
        if not self._opened:
            raise RuntimeError("in-memory bootstrap state store is not open")

    def open(self) -> None:
        self._opened = True

    def initialize(self, now: str) -> None:
        self._require_open()
        if self._state is None:
            self._state = BootstrapState(
                reset_epoch=0,
                claim_state=ClaimState.UNCLAIMED,
                network_state=NetworkState.UNCONFIGURED,
                workspace_state=WorkspaceState.ABSENT,
                owner_id=None,
                updated_at=now,
            )

    def get_state(self) -> BootstrapState:
        self._require_open()
        if self._state is None:
            raise RuntimeError("in-memory bootstrap state store is not initialized")
        return self._state

    def issue_commissioning_session(
        self,
        *,
        session_id: str,
        secret_hash: str,
        created_at: str,
        expires_at: str,
    ) -> None:
        self._require_open()
        self._sessions = [
            (
                replace(metadata, revoked_at=created_at)
                if metadata.consumed_at is None and metadata.revoked_at is None
                else metadata,
                stored_hash,
            )
            for metadata, stored_hash in self._sessions
        ]
        self._sessions.append(
            (
                CommissioningSessionMetadata(
                    session_id=session_id,
                    created_at=created_at,
                    expires_at=expires_at,
                ),
                secret_hash,
            )
        )

    def latest_commissioning_session(
        self,
    ) -> CommissioningSessionMetadata | None:
        self._require_open()
        if not self._sessions:
            return None
        return self._sessions[-1][0]

    def authorize_commissioning_session(
        self,
        *,
        session_id: str,
        secret_hash: str,
        now: str,
    ) -> CommissioningSessionMetadata:
        self._require_open()
        for index, (metadata, stored_hash) in enumerate(self._sessions):
            if metadata.session_id != session_id:
                continue
            if (
                metadata.consumed_at is not None
                or metadata.revoked_at is not None
                or metadata.expires_at <= now
            ):
                break
            if hmac.compare_digest(stored_hash, secret_hash):
                return metadata
            failed_attempts = metadata.failed_attempts + 1
            self._sessions[index] = (
                replace(
                    metadata,
                    failed_attempts=failed_attempts,
                    revoked_at=(
                        now
                        if failed_attempts >= MAX_COMMISSIONING_FAILED_ATTEMPTS
                        else None
                    ),
                ),
                stored_hash,
            )
            break
        raise BootstrapStateConflict("commissioning session is unavailable")

    def claim_controller(
        self,
        *,
        session_id: str,
        secret_hash: str,
        grant: ControllerGrant,
        now: str,
    ) -> ControllerGrant:
        self._require_open()
        claimed_controller_id = self._session_controller_ids.get(session_id)
        if claimed_controller_id is not None:
            existing = self._controllers.get(claimed_controller_id)
            if (
                claimed_controller_id == grant.controller_id
                and existing is not None
                and existing.public_key == grant.public_key
            ):
                return existing
            raise BootstrapStateConflict("commissioning session is unavailable")
        self.authorize_commissioning_session(
            session_id=session_id,
            secret_hash=secret_hash,
            now=now,
        )
        state = self.get_state()
        if state.claim_state is not ClaimState.UNCLAIMED:
            raise BootstrapStateConflict("host is already claimed")
        if state.network_state is not NetworkState.CONNECTED:
            raise BootstrapStateConflict("network must be connected before claim")
        if grant.reset_epoch != state.reset_epoch:
            raise BootstrapStateConflict("controller reset epoch does not match host")
        if grant.controller_id in self._controllers:
            raise BootstrapStateConflict("controller already exists")
        self._controllers[grant.controller_id] = grant
        self._session_controller_ids[session_id] = grant.controller_id
        self._sessions = [
            (
                replace(metadata, consumed_at=now)
                if metadata.session_id == session_id
                else metadata,
                stored_hash,
            )
            for metadata, stored_hash in self._sessions
        ]
        assert self._state is not None
        self._state = replace(
            self._state,
            claim_state=ClaimState.CLAIMED,
            updated_at=now,
        )
        return grant

    def get_controller(self, controller_id: str) -> ControllerGrant | None:
        self._require_open()
        return self._controllers.get(controller_id)

    def list_controllers(self) -> list[ControllerGrant]:
        self._require_open()
        return sorted(
            self._controllers.values(),
            key=lambda grant: (grant.created_at, grant.controller_id),
        )

    def bind_controller_owner(
        self,
        *,
        controller_id: str,
        owner_id: str,
        reset_epoch: int,
        now: str,
    ) -> ControllerGrant:
        self._require_open()
        grant = self._controllers.get(controller_id)
        state = self.get_state()
        if (
            grant is None
            or grant.revoked_at is not None
            or grant.reset_epoch != reset_epoch
            or state.reset_epoch != reset_epoch
            or state.claim_state is not ClaimState.CLAIMED
        ):
            raise BootstrapStateConflict("controller is not authorized for this Host")
        if state.owner_id is not None and state.owner_id != owner_id:
            raise BootstrapStateConflict("Host is already bound to another Owner")
        assert self._state is not None
        self._state = replace(
            self._state,
            workspace_state=WorkspaceState.READY,
            owner_id=owner_id,
            updated_at=now,
        )
        return grant

    def create_operation(self, operation: BootstrapOperation) -> BootstrapOperation:
        self._require_open()
        current = self._operations.get(operation.operation_id)
        if current is not None:
            if (
                current.operation_type == operation.operation_type
                and current.target == operation.target
                and current.reset_epoch == operation.reset_epoch
            ):
                return current
            raise BootstrapStateConflict("operation_id is already in use")
        if operation.reset_epoch != self.get_state().reset_epoch:
            raise BootstrapStateConflict("operation reset epoch does not match host")
        active_states = {
            BootstrapOperationState.PENDING,
            BootstrapOperationState.RUNNING,
            BootstrapOperationState.WAITING_CONFIRMATION,
            BootstrapOperationState.COMPENSATING,
        }
        if any(item.state in active_states for item in self._operations.values()):
            raise BootstrapStateConflict("another bootstrap operation is active")
        self._operations[operation.operation_id] = operation
        return operation

    def get_operation(self, operation_id: str) -> BootstrapOperation | None:
        self._require_open()
        return self._operations.get(operation_id)

    def update_operation(
        self,
        operation_id: str,
        *,
        state: BootstrapOperationState,
        network_state: NetworkState,
        updated_at: str,
        error_code: str | None = None,
    ) -> BootstrapOperation:
        self._require_open()
        current = self._operations.get(operation_id)
        if current is None:
            raise BootstrapStateConflict("bootstrap operation does not exist")
        updated = replace(
            current,
            state=state,
            updated_at=updated_at,
            error_code=error_code,
        )
        self._operations[operation_id] = updated
        assert self._state is not None
        self._state = replace(
            self._state,
            network_state=network_state,
            updated_at=updated_at,
        )
        return updated

    def fail_interrupted_operations(self, *, now: str) -> int:
        self._require_open()
        active_states = {
            BootstrapOperationState.PENDING,
            BootstrapOperationState.RUNNING,
            BootstrapOperationState.WAITING_CONFIRMATION,
            BootstrapOperationState.COMPENSATING,
        }
        interrupted = [
            operation_id
            for operation_id, operation in self._operations.items()
            if operation.state in active_states
        ]
        for operation_id in interrupted:
            self._operations[operation_id] = replace(
                self._operations[operation_id],
                state=BootstrapOperationState.FAILED,
                updated_at=now,
                error_code="daemon_restarted",
            )
        if interrupted:
            assert self._state is not None
            self._state = replace(
                self._state,
                network_state=NetworkState.DEGRADED,
                updated_at=now,
            )
        return len(interrupted)

    def reconcile_network_state(
        self,
        *,
        network_state: NetworkState,
        now: str,
    ) -> None:
        self._require_open()
        if network_state in {NetworkState.STAGING, NetworkState.ROLLING_BACK}:
            raise BootstrapStateConflict(
                "cannot reconcile an active network transition at startup"
            )
        assert self._state is not None
        self._state = replace(
            self._state,
            network_state=network_state,
            updated_at=now,
        )

    def reset_authority(
        self,
        *,
        network_state: NetworkState,
        now: str,
    ) -> BootstrapState:
        self._require_open()
        self._sessions = [
            (
                replace(metadata, revoked_at=now)
                if metadata.consumed_at is None and metadata.revoked_at is None
                else metadata,
                stored_hash,
            )
            for metadata, stored_hash in self._sessions
        ]
        self._controllers = {
            controller_id: (
                replace(grant, revoked_at=now) if grant.revoked_at is None else grant
            )
            for controller_id, grant in self._controllers.items()
        }
        active_states = {
            BootstrapOperationState.PENDING,
            BootstrapOperationState.RUNNING,
            BootstrapOperationState.WAITING_CONFIRMATION,
            BootstrapOperationState.COMPENSATING,
        }
        self._operations = {
            operation_id: (
                replace(
                    operation,
                    state=BootstrapOperationState.FAILED,
                    updated_at=now,
                    error_code="authority_reset",
                )
                if operation.state in active_states
                else operation
            )
            for operation_id, operation in self._operations.items()
        }
        state = self.get_state()
        self._state = replace(
            state,
            reset_epoch=state.reset_epoch + 1,
            claim_state=ClaimState.UNCLAIMED,
            network_state=network_state,
            updated_at=now,
        )
        return self._state

    def close(self) -> None:
        self._opened = False
