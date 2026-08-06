"""SQLite adapter for the minimal durable Bootstrap authority."""

from __future__ import annotations

import os
import hmac
import sqlite3
from pathlib import Path

from ...domain import (
    BootstrapState,
    BootstrapOperation,
    BootstrapOperationState,
    BootstrapOperationType,
    ClaimState,
    CommissioningSessionMetadata,
    ControllerGrant,
    ControllerRole,
    NetworkState,
    RecoveryState,
    WorkspaceState,
)
from ...ports.state_store import (
    MAX_COMMISSIONING_FAILED_ATTEMPTS,
    BootstrapStateConflict,
)


_SCHEMA_VERSION = 4


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
        if version not in (0, 1, 2, 3, _SCHEMA_VERSION):
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
                    revoked_at TEXT,
                    claimed_controller_id TEXT,
                    failed_attempts INTEGER NOT NULL DEFAULT 0
                        CHECK (failed_attempts >= 0)
                );

                CREATE TABLE controller_grants (
                    controller_id TEXT PRIMARY KEY,
                    public_key TEXT NOT NULL,
                    public_key_fingerprint TEXT NOT NULL UNIQUE,
                    role TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    reset_epoch INTEGER NOT NULL CHECK (reset_epoch >= 0),
                    created_at TEXT NOT NULL,
                    revoked_at TEXT
                );

                CREATE TABLE bootstrap_operations (
                    operation_id TEXT PRIMARY KEY,
                    operation_type TEXT NOT NULL,
                    state TEXT NOT NULL,
                    target TEXT NOT NULL,
                    reset_epoch INTEGER NOT NULL CHECK (reset_epoch >= 0),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    error_code TEXT
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
            self._migrate_v2_to_v3()
            self._migrate_v3_to_v4()
            self.connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            self.connection.commit()
        elif version == 2:
            self._migrate_v2_to_v3()
            self._migrate_v3_to_v4()
            self.connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            self.connection.commit()
        elif version == 3:
            self._migrate_v3_to_v4()
            self.connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            self.connection.commit()

    def _migrate_v2_to_v3(self) -> None:
        self.connection.executescript(
            """
            ALTER TABLE commissioning_sessions
                ADD COLUMN claimed_controller_id TEXT;

            CREATE TABLE controller_grants (
                controller_id TEXT PRIMARY KEY,
                public_key TEXT NOT NULL,
                public_key_fingerprint TEXT NOT NULL UNIQUE,
                role TEXT NOT NULL,
                display_name TEXT NOT NULL,
                platform TEXT NOT NULL,
                reset_epoch INTEGER NOT NULL CHECK (reset_epoch >= 0),
                created_at TEXT NOT NULL,
                revoked_at TEXT
            );

            CREATE TABLE bootstrap_operations (
                operation_id TEXT PRIMARY KEY,
                operation_type TEXT NOT NULL,
                state TEXT NOT NULL,
                target TEXT NOT NULL,
                reset_epoch INTEGER NOT NULL CHECK (reset_epoch >= 0),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                error_code TEXT
            );
            """
        )

    def _migrate_v3_to_v4(self) -> None:
        self.connection.execute(
            """
            ALTER TABLE commissioning_sessions
                ADD COLUMN failed_attempts INTEGER NOT NULL DEFAULT 0
                    CHECK (failed_attempts >= 0)
            """
        )

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
            SELECT session_id, created_at, expires_at, consumed_at, revoked_at,
                   failed_attempts
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
            failed_attempts=int(row["failed_attempts"]),
        )

    def authorize_commissioning_session(
        self,
        *,
        session_id: str,
        secret_hash: str,
        now: str,
    ) -> CommissioningSessionMetadata:
        row = self.connection.execute(
            "SELECT * FROM commissioning_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if (
            row is None
            or row["consumed_at"] is not None
            or row["revoked_at"] is not None
            or row["expires_at"] <= now
        ):
            raise BootstrapStateConflict("commissioning session is unavailable")
        if not hmac.compare_digest(row["secret_hash"], secret_hash):
            failed_attempts = int(row["failed_attempts"]) + 1
            revoked_at = (
                now if failed_attempts >= MAX_COMMISSIONING_FAILED_ATTEMPTS else None
            )
            with self.connection:
                self.connection.execute(
                    """
                    UPDATE commissioning_sessions
                       SET failed_attempts = ?, revoked_at = ?
                     WHERE session_id = ?
                    """,
                    (failed_attempts, revoked_at, session_id),
                )
            raise BootstrapStateConflict("commissioning session is unavailable")
        return CommissioningSessionMetadata(
            session_id=row["session_id"],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            consumed_at=row["consumed_at"],
            revoked_at=row["revoked_at"],
            failed_attempts=int(row["failed_attempts"]),
        )

    def claim_controller(
        self,
        *,
        session_id: str,
        secret_hash: str,
        grant: ControllerGrant,
        now: str,
    ) -> ControllerGrant:
        with self.connection:
            session = self.connection.execute(
                "SELECT * FROM commissioning_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if session is not None and session["consumed_at"] is not None:
                if session["claimed_controller_id"] == grant.controller_id:
                    existing = self.get_controller(grant.controller_id)
                    if existing is not None and existing.public_key == grant.public_key:
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
                raise BootstrapStateConflict(
                    "controller reset epoch does not match host"
                )
            self.connection.execute(
                """
                INSERT INTO controller_grants (
                    controller_id, public_key, public_key_fingerprint, role,
                    display_name, platform, reset_epoch, created_at, revoked_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    grant.controller_id,
                    grant.public_key,
                    grant.public_key_fingerprint,
                    grant.role.value,
                    grant.display_name,
                    grant.platform,
                    grant.reset_epoch,
                    grant.created_at,
                    grant.revoked_at,
                ),
            )
            self.connection.execute(
                """
                UPDATE commissioning_sessions
                   SET consumed_at = ?, claimed_controller_id = ?
                 WHERE session_id = ?
                """,
                (now, grant.controller_id, session_id),
            )
            self.connection.execute(
                """
                UPDATE bootstrap_state
                   SET claim_state = ?, updated_at = ?
                 WHERE singleton = 1
                """,
                (ClaimState.CLAIMED.value, now),
            )
        return grant

    def get_controller(self, controller_id: str) -> ControllerGrant | None:
        row = self.connection.execute(
            "SELECT * FROM controller_grants WHERE controller_id = ?",
            (controller_id,),
        ).fetchone()
        return None if row is None else self._controller_from_row(row)

    def list_controllers(self) -> list[ControllerGrant]:
        rows = self.connection.execute(
            "SELECT * FROM controller_grants ORDER BY created_at, controller_id"
        ).fetchall()
        return [self._controller_from_row(row) for row in rows]

    def create_operation(self, operation: BootstrapOperation) -> BootstrapOperation:
        current = self.get_operation(operation.operation_id)
        if current is not None:
            if (
                current.operation_type == operation.operation_type
                and current.target == operation.target
                and current.reset_epoch == operation.reset_epoch
            ):
                return current
            raise BootstrapStateConflict("operation_id is already in use")
        state = self.get_state()
        if operation.reset_epoch != state.reset_epoch:
            raise BootstrapStateConflict("operation reset epoch does not match host")
        with self.connection:
            active = self.connection.execute(
                """
                SELECT operation_id FROM bootstrap_operations
                 WHERE state IN (?, ?, ?, ?)
                 LIMIT 1
                """,
                (
                    BootstrapOperationState.PENDING.value,
                    BootstrapOperationState.RUNNING.value,
                    BootstrapOperationState.WAITING_CONFIRMATION.value,
                    BootstrapOperationState.COMPENSATING.value,
                ),
            ).fetchone()
            if active is not None:
                raise BootstrapStateConflict("another bootstrap operation is active")
            self.connection.execute(
                """
                INSERT INTO bootstrap_operations (
                    operation_id, operation_type, state, target, reset_epoch,
                    created_at, updated_at, error_code
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    operation.operation_id,
                    operation.operation_type.value,
                    operation.state.value,
                    operation.target,
                    operation.reset_epoch,
                    operation.created_at,
                    operation.updated_at,
                    operation.error_code,
                ),
            )
        return operation

    def get_operation(self, operation_id: str) -> BootstrapOperation | None:
        row = self.connection.execute(
            "SELECT * FROM bootstrap_operations WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        return None if row is None else self._operation_from_row(row)

    def update_operation(
        self,
        operation_id: str,
        *,
        state: BootstrapOperationState,
        network_state: NetworkState,
        updated_at: str,
        error_code: str | None = None,
    ) -> BootstrapOperation:
        with self.connection:
            current = self.get_operation(operation_id)
            if current is None:
                raise BootstrapStateConflict("bootstrap operation does not exist")
            self.connection.execute(
                """
                UPDATE bootstrap_operations
                   SET state = ?, updated_at = ?, error_code = ?
                 WHERE operation_id = ?
                """,
                (state.value, updated_at, error_code, operation_id),
            )
            self.connection.execute(
                """
                UPDATE bootstrap_state
                   SET network_state = ?, updated_at = ?
                 WHERE singleton = 1
                """,
                (network_state.value, updated_at),
            )
        result = self.get_operation(operation_id)
        assert result is not None
        return result

    def fail_interrupted_operations(self, *, now: str) -> int:
        active_states = (
            BootstrapOperationState.PENDING.value,
            BootstrapOperationState.RUNNING.value,
            BootstrapOperationState.WAITING_CONFIRMATION.value,
            BootstrapOperationState.COMPENSATING.value,
        )
        with self.connection:
            cursor = self.connection.execute(
                """
                UPDATE bootstrap_operations
                   SET state = ?, updated_at = ?, error_code = ?
                 WHERE state IN (?, ?, ?, ?)
                """,
                (
                    BootstrapOperationState.FAILED.value,
                    now,
                    "daemon_restarted",
                    *active_states,
                ),
            )
            interrupted = cursor.rowcount
            if interrupted:
                self.connection.execute(
                    """
                    UPDATE bootstrap_state
                       SET network_state = ?, updated_at = ?
                     WHERE singleton = 1
                    """,
                    (NetworkState.DEGRADED.value, now),
                )
        return interrupted

    def reconcile_network_state(
        self,
        *,
        network_state: NetworkState,
        now: str,
    ) -> None:
        if network_state in {NetworkState.STAGING, NetworkState.ROLLING_BACK}:
            raise BootstrapStateConflict(
                "cannot reconcile an active network transition at startup"
            )
        with self.connection:
            self.connection.execute(
                """
                UPDATE bootstrap_state
                   SET network_state = ?, updated_at = ?
                 WHERE singleton = 1
                """,
                (network_state.value, now),
            )

    @staticmethod
    def _controller_from_row(row: sqlite3.Row) -> ControllerGrant:
        try:
            return ControllerGrant(
                controller_id=row["controller_id"],
                public_key=row["public_key"],
                public_key_fingerprint=row["public_key_fingerprint"],
                role=ControllerRole(row["role"]),
                display_name=row["display_name"],
                platform=row["platform"],
                reset_epoch=int(row["reset_epoch"]),
                created_at=row["created_at"],
                revoked_at=row["revoked_at"],
            )
        except ValueError as exc:
            raise SQLiteBootstrapStoreError(
                "controller grant contains an unknown enum"
            ) from exc

    @staticmethod
    def _operation_from_row(row: sqlite3.Row) -> BootstrapOperation:
        try:
            return BootstrapOperation(
                operation_id=row["operation_id"],
                operation_type=BootstrapOperationType(row["operation_type"]),
                state=BootstrapOperationState(row["state"]),
                target=row["target"],
                reset_epoch=int(row["reset_epoch"]),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                error_code=row["error_code"],
            )
        except ValueError as exc:
            raise SQLiteBootstrapStoreError(
                "bootstrap operation contains an unknown enum"
            ) from exc

    def close(self) -> None:
        if self._connection is None:
            return
        self._connection.close()
        self._connection = None
