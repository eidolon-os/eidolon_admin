"""Durable authority required by Bootstrap, independent of storage format."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..domain import BootstrapState, CommissioningSessionMetadata


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

    def close(self) -> None: ...
