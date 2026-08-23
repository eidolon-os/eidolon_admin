"""Workflow-owned durable RemovalIntent ledger; never a copy of Authority state."""

from __future__ import annotations

import hashlib
import sqlite3
import threading
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid5

from .contracts import DeviceRef, HubClaimRevocationResult
from ...lifecycle_workflow.protocol import removal_intent_id

_COMMAND_NAMESPACE = UUID("2c33cc48-d0ac-5f51-a685-b3d299aeb5cd")


@dataclass(frozen=True, slots=True)
class RemovalIntent:
    intent_id: str
    ingress_request_id: str
    owner_domain_id: str
    device_ref: DeviceRef
    actor_controller_id: str
    workload_principal_id: str
    controller_reset_epoch: int
    authorization_context_json: str
    authorization_context_sha256: str
    reason: str
    hub_command_id: str
    state: str
    created_at: datetime
    updated_at: datetime
    hub_result: HubClaimRevocationResult | None = None


class RemovalIntentStore(Protocol):
    def get_or_create(
        self,
        *,
        ingress_request_id: str,
        owner_domain_id: str,
        device_ref: DeviceRef,
        actor_controller_id: str,
        workload_principal_id: str,
        controller_reset_epoch: int,
        authorization_context_sha256: str,
        authorization_context_json: str,
        reason: str,
        now: datetime,
    ) -> RemovalIntent: ...

    def mark_hub_committed(
        self,
        *,
        intent_id: str,
        result: HubClaimRevocationResult,
        now: datetime,
    ) -> RemovalIntent: ...


def _hub_command_id(intent_id: str, device_ref: DeviceRef) -> str:
    value = uuid5(
        _COMMAND_NAMESPACE,
        "eidolon-revoke-claim-v1:"
        f"{intent_id}:{device_ref.device_instance_id}:"
        f"{device_ref.owner_domain_generation}:"
        f"{device_ref.claim_generation}:{device_ref.trust_epoch}",
    )
    return f"revoke-claim-{value.hex}"


class InMemoryRemovalIntentStore:
    def __init__(self) -> None:
        self._values: dict[str, RemovalIntent] = {}

    def get_or_create(self, **values) -> RemovalIntent:
        if hashlib.sha256(values["authorization_context_json"].encode()).hexdigest() != (
            values["authorization_context_sha256"]
        ):
            raise ValueError("authorization context bytes do not match their hash")
        intent_id = removal_intent_id(
            ingress_request_id=values["ingress_request_id"],
            owner_domain_id=values["owner_domain_id"],
        )
        current = self._values.get(intent_id)
        if current is not None:
            if (
                current.device_ref != values["device_ref"]
                or current.reason != values["reason"]
                or current.actor_controller_id != values["actor_controller_id"]
                or current.workload_principal_id != values["workload_principal_id"]
                or current.controller_reset_epoch != values["controller_reset_epoch"]
                or current.authorization_context_sha256
                != values["authorization_context_sha256"]
                or current.authorization_context_json
                != values["authorization_context_json"]
            ):
                raise ValueError("request_id was reused with different removal content")
            return current
        intent = RemovalIntent(
            intent_id=intent_id,
            ingress_request_id=values["ingress_request_id"],
            owner_domain_id=values["owner_domain_id"],
            device_ref=values["device_ref"],
            actor_controller_id=values["actor_controller_id"],
            workload_principal_id=values["workload_principal_id"],
            controller_reset_epoch=values["controller_reset_epoch"],
            authorization_context_json=values["authorization_context_json"],
            authorization_context_sha256=values["authorization_context_sha256"],
            reason=values["reason"],
            hub_command_id=_hub_command_id(intent_id, values["device_ref"]),
            state="accepted",
            created_at=values["now"],
            updated_at=values["now"],
        )
        self._values[intent_id] = intent
        return intent

    def mark_hub_committed(self, *, intent_id, result, now) -> RemovalIntent:
        current = self._values[intent_id]
        if current.hub_result is not None and current.hub_result != result:
            raise ValueError("Hub result changed for an existing RemovalIntent")
        updated = replace(
            current, state="hub-revoked", updated_at=now, hub_result=result
        )
        self._values[intent_id] = updated
        return updated


class SqliteRemovalIntentStore:
    """Strict ledger that freezes one DeviceRef per Owner ingress request."""

    _COLUMNS = {
        "intent_id",
        "ingress_request_id",
        "owner_domain_id",
        "device_id",
        "owner_domain_generation",
        "claim_generation",
        "trust_epoch",
        "device_ref_json",
        "actor_controller_id",
        "workload_principal_id",
        "controller_reset_epoch",
        "authorization_context_json",
        "authorization_context_sha256",
        "reason",
        "hub_command_id",
        "state",
        "hub_result_json",
        "created_at",
        "updated_at",
    }

    def __init__(self, path: Path) -> None:
        self._path = path.resolve()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._mutex = threading.RLock()
        self._connection = sqlite3.connect(
            self._path, isolation_level=None, check_same_thread=False
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA busy_timeout=5000")
        try:
            self._initialize()
        except Exception:
            self._connection.close()
            raise

    def _initialize(self) -> None:
        with self._mutex:
            existed = self._connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='removal_intents'"
            ).fetchone()
            version = self._connection.execute("PRAGMA user_version").fetchone()[0]
            if existed is not None and version == 0:
                self._migrate_generation_scoped_draft()
                version = 1
            if existed is not None and version == 1:
                self._migrate_authorization_context()
                version = 2
            if existed is not None and version == 2:
                self._migrate_authorization_context_bytes()
                version = 3
            if existed is not None and version == 3:
                self._migrate_owner_domain_generation()
                version = 4
            if existed is not None and version != 4:
                raise RuntimeError("Admin RemovalIntent schema version is unknown")
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS removal_intents (
                    intent_id TEXT PRIMARY KEY,
                    ingress_request_id TEXT NOT NULL,
                    owner_domain_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    owner_domain_generation INTEGER NOT NULL,
                    claim_generation INTEGER NOT NULL,
                    trust_epoch INTEGER NOT NULL,
                    device_ref_json TEXT NOT NULL,
                    actor_controller_id TEXT NOT NULL,
                    workload_principal_id TEXT NOT NULL,
                    controller_reset_epoch INTEGER NOT NULL CHECK(controller_reset_epoch >= 0),
                    authorization_context_json TEXT NOT NULL,
                    authorization_context_sha256 TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    hub_command_id TEXT NOT NULL UNIQUE,
                    state TEXT NOT NULL,
                    hub_result_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(owner_domain_id, ingress_request_id)
                );
                """
            )
            if existed is None:
                self._connection.execute("PRAGMA user_version=4")
            columns = {
                row[1]
                for row in self._connection.execute(
                    "PRAGMA table_info(removal_intents)"
                )
            }
            if columns != self._COLUMNS:
                raise RuntimeError("Admin RemovalIntent schema is partial or unknown")
            unique_indexes = {
                tuple(
                    item[2]
                    for item in self._connection.execute(
                        f"PRAGMA index_info('{row[1]}')"
                    )
                )
                for row in self._connection.execute(
                    "PRAGMA index_list('removal_intents')"
                )
                if row[2]
            }
            if ("owner_domain_id", "ingress_request_id") not in unique_indexes:
                raise RuntimeError("Admin RemovalIntent idempotency constraint is unknown")

    def _migrate_generation_scoped_draft(self) -> None:
        columns = {
            row[1]
            for row in self._connection.execute("PRAGMA table_info(removal_intents)")
        }
        unique_indexes = {
            tuple(
                item[2]
                for item in self._connection.execute(
                    f"PRAGMA index_info('{row[1]}')"
                )
            )
            for row in self._connection.execute("PRAGMA index_list('removal_intents')")
            if row[2]
        }
        old_key = (
            "owner_domain_id",
            "device_id",
            "claim_generation",
            "ingress_request_id",
        )
        legacy_columns = self._COLUMNS - {
            "owner_domain_generation",
            "workload_principal_id",
            "controller_reset_epoch",
            "authorization_context_sha256",
        }
        if columns != legacy_columns or old_key not in unique_indexes:
            raise RuntimeError("Admin RemovalIntent schema version is unknown")
        self._connection.executescript(
            """
            BEGIN IMMEDIATE;
            ALTER TABLE removal_intents RENAME TO removal_intents_v0;
            CREATE TABLE removal_intents (
                intent_id TEXT PRIMARY KEY,
                ingress_request_id TEXT NOT NULL,
                owner_domain_id TEXT NOT NULL,
                device_id TEXT NOT NULL,
                claim_generation INTEGER NOT NULL,
                trust_epoch INTEGER NOT NULL,
                device_ref_json TEXT NOT NULL,
                actor_controller_id TEXT NOT NULL,
                reason TEXT NOT NULL,
                hub_command_id TEXT NOT NULL UNIQUE,
                state TEXT NOT NULL,
                hub_result_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(owner_domain_id, ingress_request_id)
            );
            INSERT INTO removal_intents SELECT * FROM removal_intents_v0;
            DROP TABLE removal_intents_v0;
            PRAGMA user_version=1;
            COMMIT;
            """
        )

    def _migrate_authorization_context(self) -> None:
        columns = {
            row[1]
            for row in self._connection.execute("PRAGMA table_info(removal_intents)")
        }
        legacy_columns = self._COLUMNS - {
            "owner_domain_generation",
            "workload_principal_id",
            "controller_reset_epoch",
            "authorization_context_sha256",
        }
        if columns != legacy_columns:
            raise RuntimeError("Admin RemovalIntent schema version is unknown")
        legacy_rows = self._connection.execute(
            "SELECT COUNT(*) FROM removal_intents"
        ).fetchone()[0]
        if legacy_rows:
            raise RuntimeError(
                "RemovalIntent rows without a bound authorization context "
                "require an offline verified migration"
            )
        self._connection.executescript(
            """
            BEGIN IMMEDIATE;
            ALTER TABLE removal_intents
                ADD COLUMN workload_principal_id TEXT NOT NULL DEFAULT '';
            ALTER TABLE removal_intents
                ADD COLUMN controller_reset_epoch INTEGER NOT NULL DEFAULT 0;
            ALTER TABLE removal_intents ADD COLUMN authorization_context_sha256
                TEXT NOT NULL DEFAULT '';
            PRAGMA user_version=2;
            COMMIT;
            """
        )

    def _migrate_authorization_context_bytes(self) -> None:
        columns = {
            row[1]
            for row in self._connection.execute("PRAGMA table_info(removal_intents)")
        }
        legacy_columns = self._COLUMNS - {
            "owner_domain_generation",
            "authorization_context_json",
        }
        if columns != legacy_columns:
            raise RuntimeError("Admin RemovalIntent schema version is unknown")
        legacy_rows = self._connection.execute(
            "SELECT COUNT(*) FROM removal_intents"
        ).fetchone()[0]
        if legacy_rows:
            raise RuntimeError(
                "RemovalIntent rows without canonical authorization bytes "
                "require an offline verified migration"
            )
        self._connection.executescript(
            """
            BEGIN IMMEDIATE;
            ALTER TABLE removal_intents ADD COLUMN authorization_context_json
                TEXT NOT NULL DEFAULT '';
            PRAGMA user_version=3;
            COMMIT;
            """
        )

    def _migrate_owner_domain_generation(self) -> None:
        columns = {
            row[1]
            for row in self._connection.execute("PRAGMA table_info(removal_intents)")
        }
        if columns != self._COLUMNS - {"owner_domain_generation"}:
            raise RuntimeError("Admin RemovalIntent schema version is unknown")
        self._connection.executescript(
            """
            BEGIN IMMEDIATE;
            ALTER TABLE removal_intents ADD COLUMN owner_domain_generation
                INTEGER NOT NULL DEFAULT 1;
            UPDATE removal_intents SET device_ref_json = json_set(
                device_ref_json, '$.owner_domain_generation', 1
            );
            PRAGMA user_version=4;
            COMMIT;
            """
        )

    @staticmethod
    def _timestamp(value: datetime) -> str:
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _decode(row: sqlite3.Row) -> RemovalIntent:
        return RemovalIntent(
            intent_id=row["intent_id"],
            ingress_request_id=row["ingress_request_id"],
            owner_domain_id=row["owner_domain_id"],
            device_ref=DeviceRef.model_validate_json(row["device_ref_json"]),
            actor_controller_id=row["actor_controller_id"],
            workload_principal_id=row["workload_principal_id"],
            controller_reset_epoch=int(row["controller_reset_epoch"]),
            authorization_context_json=row["authorization_context_json"],
            authorization_context_sha256=row["authorization_context_sha256"],
            reason=row["reason"],
            hub_command_id=row["hub_command_id"],
            state=row["state"],
            created_at=datetime.fromisoformat(row["created_at"].replace("Z", "+00:00")),
            updated_at=datetime.fromisoformat(row["updated_at"].replace("Z", "+00:00")),
            hub_result=(
                None
                if row["hub_result_json"] is None
                else HubClaimRevocationResult.model_validate_json(
                    row["hub_result_json"]
                )
            ),
        )

    def get_or_create(
        self,
        *,
        ingress_request_id: str,
        owner_domain_id: str,
        device_ref: DeviceRef,
        actor_controller_id: str,
        workload_principal_id: str,
        controller_reset_epoch: int,
        authorization_context_json: str,
        authorization_context_sha256: str,
        reason: str,
        now: datetime,
    ) -> RemovalIntent:
        if hashlib.sha256(authorization_context_json.encode()).hexdigest() != (
            authorization_context_sha256
        ):
            raise ValueError("authorization context bytes do not match their hash")
        intent_id = removal_intent_id(
            ingress_request_id=ingress_request_id,
            owner_domain_id=owner_domain_id,
        )
        with self._mutex:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    """SELECT * FROM removal_intents
                         WHERE owner_domain_id = ? AND ingress_request_id = ?""",
                    (owner_domain_id, ingress_request_id),
                ).fetchone()
                if row is not None:
                    current = self._decode(row)
                    if (
                        current.device_ref != device_ref
                        or current.reason != reason
                        or current.actor_controller_id != actor_controller_id
                        or current.workload_principal_id != workload_principal_id
                        or current.controller_reset_epoch != controller_reset_epoch
                        or current.authorization_context_sha256
                        != authorization_context_sha256
                        or current.authorization_context_json
                        != authorization_context_json
                    ):
                        raise ValueError(
                            "request_id was reused with different removal content"
                        )
                    self._connection.execute("COMMIT")
                    return current
                command_id = _hub_command_id(intent_id, device_ref)
                timestamp = self._timestamp(now)
                self._connection.execute(
                    """INSERT INTO removal_intents(
                        intent_id, ingress_request_id, owner_domain_id, device_id,
                        owner_domain_generation, claim_generation, trust_epoch,
                        device_ref_json,
                        actor_controller_id, workload_principal_id,
                        controller_reset_epoch, authorization_context_json,
                        authorization_context_sha256,
                        reason, hub_command_id, state,
                        hub_result_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)""",
                    (
                        intent_id,
                        ingress_request_id,
                        owner_domain_id,
                        device_ref.device_instance_id,
                        device_ref.owner_domain_generation,
                        device_ref.claim_generation,
                        device_ref.trust_epoch,
                        device_ref.model_dump_json(),
                        actor_controller_id,
                        workload_principal_id,
                        controller_reset_epoch,
                        authorization_context_json,
                        authorization_context_sha256,
                        reason,
                        command_id,
                        "accepted",
                        timestamp,
                        timestamp,
                    ),
                )
                self._connection.execute("COMMIT")
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise
        return RemovalIntent(
            intent_id=intent_id,
            ingress_request_id=ingress_request_id,
            owner_domain_id=owner_domain_id,
            device_ref=device_ref,
            actor_controller_id=actor_controller_id,
            workload_principal_id=workload_principal_id,
            controller_reset_epoch=controller_reset_epoch,
            authorization_context_json=authorization_context_json,
            authorization_context_sha256=authorization_context_sha256,
            reason=reason,
            hub_command_id=command_id,
            state="accepted",
            created_at=now,
            updated_at=now,
        )

    def mark_hub_committed(
        self,
        *,
        intent_id: str,
        result: HubClaimRevocationResult,
        now: datetime,
    ) -> RemovalIntent:
        with self._mutex:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    "SELECT * FROM removal_intents WHERE intent_id = ?",
                    (intent_id,),
                ).fetchone()
                if row is None:
                    raise KeyError(intent_id)
                current = self._decode(row)
                if current.hub_result is not None and current.hub_result != result:
                    raise ValueError("Hub result changed for an existing RemovalIntent")
                self._connection.execute(
                    """UPDATE removal_intents
                          SET state='hub-revoked', hub_result_json=?, updated_at=?
                        WHERE intent_id=?""",
                    (
                        result.model_dump_json(),
                        self._timestamp(now),
                        intent_id,
                    ),
                )
                self._connection.execute("COMMIT")
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise
        return replace(
            current, state="hub-revoked", hub_result=result, updated_at=now
        )

    def close(self) -> None:
        with self._mutex:
            self._connection.close()
