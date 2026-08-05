"""Exclusive SQLite authority for bootstrap state and operation metadata."""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from ...domain import (
    BootstrapState,
    ClaimState,
    NetworkState,
    RecoveryState,
    WorkspaceState,
)


_SCHEMA_VERSION = 1


class BootstrapStoreError(RuntimeError):
    """Raised when bootstrap persistence is unknown or inconsistent."""


@dataclass(frozen=True, slots=True)
class CommissioningSessionMetadata:
    session_id: str
    created_at: str
    expires_at: str
    consumed_at: str | None
    revoked_at: str | None

    def to_dict(self) -> dict[str, str | bool | None]:
        return {
            "session_id": self.session_id,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "consumed_at": self.consumed_at,
            "revoked_at": self.revoked_at,
        }


class BootstrapStore:
    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path
        self._connection: sqlite3.Connection | None = None

    @property
    def connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise BootstrapStoreError("bootstrap store is not open")
        return self._connection

    def open(self) -> None:
        self._database_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        os.chmod(self._database_path, 0o600)
        self._connection = connection

    def initialize_schema(self, now: str) -> None:
        version = int(self.connection.execute("PRAGMA user_version").fetchone()[0])
        if version not in (0, _SCHEMA_VERSION):
            raise BootstrapStoreError(
                f"unsupported bootstrap schema version {version}; expected {_SCHEMA_VERSION}"
            )
        if version == 0:
            self.connection.executescript(
                """
                CREATE TABLE bootstrap_state (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    reset_epoch INTEGER NOT NULL CHECK (reset_epoch >= 0),
                    claim_state TEXT NOT NULL,
                    network_state TEXT NOT NULL,
                    workspace_state TEXT NOT NULL,
                    recovery_state TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE daemon_runs (
                    run_id TEXT PRIMARY KEY,
                    pid INTEGER NOT NULL,
                    started_at TEXT NOT NULL,
                    stopped_at TEXT
                );

                CREATE TABLE commissioning_sessions (
                    session_id TEXT PRIMARY KEY,
                    secret_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT,
                    revoked_at TEXT
                );

                CREATE INDEX commissioning_sessions_created_idx
                    ON commissioning_sessions(created_at DESC);
                """
            )
            self.connection.execute(
                """
                INSERT INTO bootstrap_state (
                    singleton, reset_epoch, claim_state, network_state,
                    workspace_state, recovery_state, updated_at
                ) VALUES (1, 0, ?, ?, ?, ?, ?)
                """,
                (
                    ClaimState.UNCLAIMED.value,
                    NetworkState.UNCONFIGURED.value,
                    WorkspaceState.ABSENT.value,
                    RecoveryState.NORMAL.value,
                    now,
                ),
            )
            self.connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            self.connection.commit()

    def get_state(self) -> BootstrapState:
        row = self.connection.execute(
            "SELECT * FROM bootstrap_state WHERE singleton = 1"
        ).fetchone()
        if row is None:
            raise BootstrapStoreError("bootstrap state singleton is missing")
        try:
            return BootstrapState(
                reset_epoch=int(row["reset_epoch"]),
                claim_state=ClaimState(row["claim_state"]),
                network_state=NetworkState(row["network_state"]),
                workspace_state=WorkspaceState(row["workspace_state"]),
                recovery_state=RecoveryState(row["recovery_state"]),
                updated_at=row["updated_at"],
            )
        except ValueError as exc:
            raise BootstrapStoreError("bootstrap state contains an unknown enum") from exc

    def record_daemon_start(
        self, *, run_id: str, pid: int, started_at: str
    ) -> None:
        self.connection.execute(
            "INSERT INTO daemon_runs (run_id, pid, started_at) VALUES (?, ?, ?)",
            (run_id, pid, started_at),
        )
        # Restart history is diagnostic, not a sovereign audit stream. Bound it
        # so a persistent crash loop cannot grow the early-boot database forever.
        self.connection.execute(
            """
            DELETE FROM daemon_runs
             WHERE run_id IN (
                 SELECT run_id
                   FROM daemon_runs
                  ORDER BY started_at DESC, run_id DESC
                  LIMIT -1 OFFSET 1024
             )
            """
        )
        self.connection.commit()

    def record_daemon_stop(self, *, run_id: str, stopped_at: str) -> None:
        self.connection.execute(
            "UPDATE daemon_runs SET stopped_at = ? WHERE run_id = ?",
            (stopped_at, run_id),
        )
        self.connection.commit()

    def issue_commissioning_session(
        self,
        *,
        session_id: str,
        secret_hash: str,
        created_at: str,
        expires_at: str,
    ) -> None:
        self.connection.execute(
            """
            UPDATE commissioning_sessions
               SET revoked_at = ?
             WHERE consumed_at IS NULL AND revoked_at IS NULL
            """,
            (created_at,),
        )
        self.connection.execute(
            """
            INSERT INTO commissioning_sessions (
                session_id, secret_hash, created_at, expires_at
            ) VALUES (?, ?, ?, ?)
            """,
            (session_id, secret_hash, created_at, expires_at),
        )
        self.connection.commit()

    def latest_commissioning_session(
        self,
    ) -> CommissioningSessionMetadata | None:
        row = self.connection.execute(
            """
            SELECT session_id, created_at, expires_at, consumed_at, revoked_at
              FROM commissioning_sessions
             ORDER BY created_at DESC, session_id DESC
             LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        return CommissioningSessionMetadata(
            session_id=row["session_id"],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            consumed_at=row["consumed_at"],
            revoked_at=row["revoked_at"],
        )

    def close(self) -> None:
        if self._connection is None:
            return
        self._connection.close()
        self._connection = None
