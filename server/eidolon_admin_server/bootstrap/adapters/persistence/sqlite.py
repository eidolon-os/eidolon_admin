"""SQLite adapter for the minimal durable Bootstrap authority."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from ...domain import (
    BootstrapState,
    ClaimState,
    CommissioningSessionMetadata,
    NetworkState,
    RecoveryState,
    WorkspaceState,
)


_SCHEMA_VERSION = 2


class SQLiteBootstrapStoreError(RuntimeError):
    """Raised when bootstrap persistence is unknown or inconsistent."""


class SQLiteBootstrapStateStore:
    """SQLite implementation of the minimal durable Bootstrap authority."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path
        self._connection: sqlite3.Connection | None = None

    @property
    def connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise SQLiteBootstrapStoreError("bootstrap store is not open")
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

    def initialize(self, now: str) -> None:
        version = int(self.connection.execute("PRAGMA user_version").fetchone()[0])
        if version not in (0, 1, _SCHEMA_VERSION):
            raise SQLiteBootstrapStoreError(
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
        elif version == 1:
            # v1 stored daemon lifecycle diagnostics in the authority database.
            # systemd/journald is the correct owner, so the v2 migration removes
            # only that diagnostic table and preserves all durable product state.
            self.connection.execute("DROP TABLE IF EXISTS daemon_runs")
            self.connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            self.connection.commit()

    def get_state(self) -> BootstrapState:
        row = self.connection.execute(
            "SELECT * FROM bootstrap_state WHERE singleton = 1"
        ).fetchone()
        if row is None:
            raise SQLiteBootstrapStoreError("bootstrap state singleton is missing")
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
            raise SQLiteBootstrapStoreError(
                "bootstrap state contains an unknown enum"
            ) from exc

    def issue_commissioning_session(
        self,
        *,
        session_id: str,
        secret_hash: str,
        created_at: str,
        expires_at: str,
    ) -> None:
        with self.connection:
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
