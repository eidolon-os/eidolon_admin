"""In-memory state adapter for application tests and local simulations."""

from __future__ import annotations

from dataclasses import replace

from ...domain import (
    BootstrapState,
    ClaimState,
    CommissioningSessionMetadata,
    NetworkState,
    RecoveryState,
    WorkspaceState,
)


class InMemoryBootstrapStateStore:
    """A non-durable adapter; never select it for a product service unit."""

    def __init__(self) -> None:
        self._opened = False
        self._state: BootstrapState | None = None
        self._sessions: list[tuple[CommissioningSessionMetadata, str]] = []

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
                recovery_state=RecoveryState.NORMAL,
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

    def close(self) -> None:
        self._opened = False
