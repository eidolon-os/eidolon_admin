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
    CommissioningSessionSeed,
    ControllerGrant,
    ControllerRole,
    NetworkState,
    WorkspaceState,
)
from ...ports.state_store import (
    MAX_COMMISSIONING_FAILED_ATTEMPTS,
    BootstrapStateConflict,
)


BOOTSTRAP_SCHEMA_VERSION = 7
_SCHEMA_VERSION = BOOTSTRAP_SCHEMA_VERSION

#: A Grant belongs to one reset epoch, so its identity carries the epoch.
#: Written once here and reused by the fresh-create and the v6 upgrade, because
#: the two drifting apart is how the field Host ended up with a table shape no
#: test covered.
_CONTROLLER_GRANTS_DDL = """
CREATE TABLE controller_grants (
    controller_id TEXT NOT NULL,
    public_key TEXT NOT NULL,
    public_key_fingerprint TEXT NOT NULL,
    role TEXT NOT NULL,
    display_name TEXT NOT NULL,
    platform TEXT NOT NULL,
    reset_epoch INTEGER NOT NULL CHECK (reset_epoch >= 0),
    created_at TEXT NOT NULL,
    revoked_at TEXT,
    PRIMARY KEY (controller_id, reset_epoch),
    UNIQUE (public_key_fingerprint, reset_epoch)
);
"""


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
        """Bring this database to the current schema, from wherever it is.

        One ordered ladder rather than a branch per starting version. The
        branch-per-version form it replaces accepted a v5 database, ran no
        migration on it, and then stamped it as current — so a Host could sit
        on a schema nothing had upgraded and nothing would complain.
        """

        version = int(self.connection.execute("PRAGMA user_version").fetchone()[0])
        if version > _SCHEMA_VERSION:
            raise SQLiteBootstrapStoreError(
                f"unsupported bootstrap schema version {version}; expected {_SCHEMA_VERSION}"
            )
        if version == 0:
            self._create_at_current_schema(now)
        else:
            ladder = (
                self._migrate_v1_to_v2,
                self._migrate_v2_to_v3,
                self._migrate_v3_to_v4,
                self._migrate_v4_to_v5,
                self._migrate_v5_to_v6,
                self._migrate_v6_to_v7,
            )
            for step in ladder[version - 1 :]:
                step()
        self.connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
        self.connection.commit()

    def _create_at_current_schema(self, now: str) -> None:
        self.connection.executescript(
            """
                CREATE TABLE bootstrap_state (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    reset_epoch INTEGER NOT NULL CHECK (reset_epoch >= 0),
                    claim_state TEXT NOT NULL,
                    network_state TEXT NOT NULL,
                    workspace_state TEXT NOT NULL,
                    owner_id TEXT,
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
            + _CONTROLLER_GRANTS_DDL
        )
        self.connection.execute(
            """
            INSERT INTO bootstrap_state (
                singleton, reset_epoch, claim_state, network_state,
                workspace_state, owner_id, updated_at
            ) VALUES (1, 0, ?, ?, ?, NULL, ?)
            """,
            (
                ClaimState.UNCLAIMED.value,
                NetworkState.UNCONFIGURED.value,
                WorkspaceState.ABSENT.value,
                now,
            ),
        )

    def _migrate_v1_to_v2(self) -> None:
        """v1 stored daemon lifecycle diagnostics in the authority database.

        systemd/journald is the correct owner, so this removes only that
        diagnostic table and preserves all durable product state.
        """

        self.connection.execute("DROP TABLE IF EXISTS daemon_runs")

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

    def _migrate_v4_to_v5(self) -> None:
        self.connection.execute(
            """
            ALTER TABLE bootstrap_state
                ADD COLUMN owner_id TEXT
            """
        )

    def _migrate_v5_to_v6(self) -> None:
        """Drop recovery_state: it only ever held one value.

        Three of its four values had no writer anywhere, so the column carried
        the constant "normal" while a phone rendered a row promising the Host
        could report physical arming or a pending factory reset.
        """

        self.connection.execute(
            """
            ALTER TABLE bootstrap_state
                DROP COLUMN recovery_state
            """
        )

    def _migrate_v6_to_v7(self) -> None:
        """Scope Grant identity to the reset epoch that issued it.

        v6 made ``controller_id`` the primary key and ``public_key_fingerprint``
        globally unique, which turned "this phone holds the Host now" into
        "this key has been seen here once, ever". A recovery reset revokes
        Grants but keeps the rows as history, so the phone that came back with
        the same key pair — the one an Owner actually still holds — hit
        ``UNIQUE constraint failed: controller_grants.public_key_fingerprint``.
        That left the store as sqlite3.IntegrityError, past every conflict
        handler, and reached the phone as "internal_error, retryable: true"
        over a collision that could never stop colliding.

        The rows are kept: they are the only record of who held this Host. What
        they lose is the power to block the phone that comes back.
        """

        self.connection.executescript(
            "ALTER TABLE controller_grants RENAME TO controller_grants_pre_v7;"
            + _CONTROLLER_GRANTS_DDL
            + """
            INSERT INTO controller_grants (
                controller_id, public_key, public_key_fingerprint, role,
                display_name, platform, reset_epoch, created_at, revoked_at
            )
            SELECT controller_id, public_key, public_key_fingerprint, role,
                   display_name, platform, reset_epoch, created_at, revoked_at
              FROM controller_grants_pre_v7;

            DROP TABLE controller_grants_pre_v7;
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
                owner_id=row["owner_id"],
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
            # A claimed Host may still admit another phone. The session is the
            # authority — one-time, expiring, and minted only by someone who
            # already holds this Host — so refusing on claim_state alone meant
            # a second phone could be added no way but by revoking the first.
            if state.network_state is not NetworkState.CONNECTED:
                raise BootstrapStateConflict("network must be connected before claim")
            if grant.reset_epoch != state.reset_epoch:
                raise BootstrapStateConflict(
                    "controller reset epoch does not match host"
                )
            held = self._grant_in_epoch(grant, state.reset_epoch)
            if held is not None:
                # This phone already holds the Host in this epoch. Refusing the
                # request told it to start over, and starting over produced the
                # same request; the older shape of this let sqlite refuse it as
                # an integrity error and the phone read "try again later".
                self.connection.execute(
                    """
                    UPDATE commissioning_sessions
                       SET consumed_at = ?, claimed_controller_id = ?
                     WHERE session_id = ?
                    """,
                    (now, held.controller_id, session_id),
                )
                return held
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

    def _grant_in_epoch(
        self, grant: ControllerGrant, reset_epoch: int
    ) -> ControllerGrant | None:
        """The Grant this key already holds in this epoch, under either name.

        controller_id and public_key_fingerprint are both derived from the same
        public key, so a collision on one is a collision on the other. Asked
        here rather than discovered by the INSERT, because a store that speaks
        sqlite3 to its caller is a store whose refusals cannot be named.
        """

        row = self.connection.execute(
            """
            SELECT * FROM controller_grants
             WHERE reset_epoch = ?
               AND (controller_id = ? OR public_key_fingerprint = ?)
            """,
            (reset_epoch, grant.controller_id, grant.public_key_fingerprint),
        ).fetchone()
        if row is None:
            return None
        held = self._controller_from_row(row)
        if held.public_key != grant.public_key or held.revoked_at is not None:
            raise BootstrapStateConflict(
                "another Controller already holds this identity on this Host"
            )
        return held

    def get_controller(self, controller_id: str) -> ControllerGrant | None:
        row = self.connection.execute(
            "SELECT * FROM controller_grants WHERE controller_id = ? AND reset_epoch = ?",
            (controller_id, self.get_state().reset_epoch),
        ).fetchone()
        return None if row is None else self._controller_from_row(row)

    def list_controllers(self) -> list[ControllerGrant]:
        rows = self.connection.execute(
            "SELECT * FROM controller_grants ORDER BY created_at, controller_id"
        ).fetchall()
        return [self._controller_from_row(row) for row in rows]

    def revoke_controller(self, *, controller_id: str, now: str) -> ControllerGrant:
        """Withdraw one phone's authority, never the last one.

        Removing the only Controller would leave a Host nobody can manage and
        no way back except the operator's own reset, so that is the operation
        that must be asked for by name rather than arrived at by removing
        phones one at a time.
        """

        with self.connection:
            state = self.get_state()
            active = [
                grant
                for grant in self.list_controllers()
                if grant.revoked_at is None and grant.reset_epoch == state.reset_epoch
            ]
            target = next(
                (grant for grant in active if grant.controller_id == controller_id),
                None,
            )
            if target is None:
                raise BootstrapStateConflict("controller is not authorized for this Host")
            if len(active) == 1:
                raise BootstrapStateConflict(
                    "the last Controller cannot be revoked; use controller-reset"
                )
            self.connection.execute(
                """
                UPDATE controller_grants
                   SET revoked_at = ?
                 WHERE controller_id = ? AND reset_epoch = ?
                """,
                (now, controller_id, state.reset_epoch),
            )
        result = self.get_controller(controller_id)
        if result is None:
            raise SQLiteBootstrapStoreError("revoked controller grant disappeared")
        return result

    def bind_controller_owner(
        self,
        *,
        controller_id: str,
        owner_id: str,
        reset_epoch: int,
        now: str,
    ) -> ControllerGrant:
        with self.connection:
            row = self.connection.execute(
                """
                SELECT * FROM controller_grants
                 WHERE controller_id = ? AND reset_epoch = ?
                """,
                (controller_id, reset_epoch),
            ).fetchone()
            state = self.get_state()
            if (
                row is None
                or row["revoked_at"] is not None
                or int(row["reset_epoch"]) != reset_epoch
                or state.reset_epoch != reset_epoch
                or state.claim_state is not ClaimState.CLAIMED
            ):
                raise BootstrapStateConflict(
                    "controller is not authorized for this Host"
                )
            existing_owner_id = state.owner_id
            if existing_owner_id is not None and existing_owner_id != owner_id:
                raise BootstrapStateConflict("Host is already bound to another Owner")
            self.connection.execute(
                """
                UPDATE bootstrap_state
                   SET workspace_state = ?, owner_id = ?, updated_at = ?
                 WHERE singleton = 1
                """,
                (WorkspaceState.READY.value, owner_id, now),
            )
        result = self.get_controller(controller_id)
        if result is None:
            raise SQLiteBootstrapStoreError("bound controller grant disappeared")
        return result

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

    def reset_authority(
        self,
        *,
        network_state: NetworkState,
        now: str,
        recovery_session: CommissioningSessionSeed | None,
    ) -> BootstrapState:
        active_states = (
            BootstrapOperationState.PENDING.value,
            BootstrapOperationState.RUNNING.value,
            BootstrapOperationState.WAITING_CONFIRMATION.value,
            BootstrapOperationState.COMPENSATING.value,
        )
        state = self.get_state()
        with self.connection:
            self.connection.execute(
                """
                UPDATE commissioning_sessions
                   SET revoked_at = ?
                 WHERE consumed_at IS NULL AND revoked_at IS NULL
                """,
                (now,),
            )
            self.connection.execute(
                """
                UPDATE controller_grants
                   SET revoked_at = ?
                 WHERE revoked_at IS NULL
                """,
                (now,),
            )
            self.connection.execute(
                """
                UPDATE bootstrap_operations
                   SET state = ?, updated_at = ?, error_code = ?
                 WHERE state IN (?, ?, ?, ?)
                """,
                (
                    BootstrapOperationState.FAILED.value,
                    now,
                    "authority_reset",
                    *active_states,
                ),
            )
            self.connection.execute(
                """
                UPDATE bootstrap_state
                   SET reset_epoch = ?, claim_state = ?, network_state = ?,
                       updated_at = ?
                 WHERE singleton = 1
                """,
                (
                    state.reset_epoch + 1,
                    ClaimState.UNCLAIMED.value,
                    network_state.value,
                    now,
                ),
            )
            if recovery_session is not None:
                self.connection.execute(
                    """
                    INSERT INTO commissioning_sessions (
                        session_id, secret_hash, created_at, expires_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        recovery_session.session_id,
                        recovery_session.secret_hash,
                        now,
                        recovery_session.expires_at,
                    ),
                )
        return self.get_state()

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
