"""Durable Admin-owned intent/checkpoint ledger for Enrollment Decisions.

Hub remains the only owner of Proposal, Decision, Grant and Claim facts.  This
ledger stores only what the authenticated Controller asked Admin to submit and
whether Admin observed Hub's immutable Decision result.  That is enough to
resume after a process restart or a lost HTTP reply without copying Hub state.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol
from uuid import UUID, uuid5

from eidolon_sdk.device_foundation.v1 import (
    ControllerActorRef,
    DecideEnrollment,
    DecideEnrollmentResult,
)


Checkpoint = Literal["intent_recorded", "decision_committed"]
_NAMESPACE = UUID("af670012-0805-51bb-b089-3a666e8a70b4")


def _canonical(value: object) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def decision_intent_id(*, owner_domain_id: str, request_id: str) -> str:
    value = uuid5(
        _NAMESPACE,
        f"eidolon-admission-decision-intent-v1:{owner_domain_id}:{request_id}",
    )
    return f"admission-intent-{value.hex}"


def decision_command_id(*, intent_id: str, enrollment_id: str) -> str:
    value = uuid5(
        _NAMESPACE,
        f"eidolon-admission-decision-command-v1:{intent_id}:{enrollment_id}",
    )
    return f"decide-enrollment-{value.hex}"


@dataclass(frozen=True, slots=True)
class AdmissionDecisionIntent:
    intent_id: str
    ingress_request_id: str
    command_id: str
    correlation_id: str
    owner_domain_id: str
    business_owner_id: str
    enrollment_id: str
    actor_json: str
    actor_sha256: str
    decision_json: str
    decision_sha256: str
    checkpoint: Checkpoint
    result: DecideEnrollmentResult | None
    created_at: datetime
    updated_at: datetime


class AdmissionDecisionIntentStore(Protocol):
    def get_or_create(
        self,
        *,
        ingress_request_id: str,
        owner_domain_id: str,
        business_owner_id: str,
        actor: ControllerActorRef,
        decision: DecideEnrollment,
        now: datetime,
    ) -> AdmissionDecisionIntent: ...

    def mark_decision_committed(
        self,
        *,
        intent_id: str,
        result: DecideEnrollmentResult,
        now: datetime,
    ) -> AdmissionDecisionIntent: ...


class InMemoryAdmissionDecisionIntentStore:
    def __init__(self) -> None:
        self._values: dict[str, AdmissionDecisionIntent] = {}

    def get_or_create(self, **values) -> AdmissionDecisionIntent:
        candidate = _new_intent(**values)
        existing = self._values.get(candidate.intent_id)
        if existing is not None:
            _assert_same_intent(existing, candidate)
            return existing
        self._values[candidate.intent_id] = candidate
        return candidate

    def mark_decision_committed(self, *, intent_id, result, now):
        intent = self._values[intent_id]
        if intent.result is not None and intent.result != result:
            raise ValueError("Hub Decision result changed for an existing intent")
        committed = _committed(intent, result=result, now=now)
        self._values[intent_id] = committed
        return committed


class SqliteAdmissionDecisionIntentStore:
    """Crash-safe Decision intent store with fail-closed schema validation."""

    _COLUMNS = {
        "intent_id",
        "ingress_request_id",
        "command_id",
        "correlation_id",
        "owner_domain_id",
        "business_owner_id",
        "enrollment_id",
        "actor_json",
        "actor_sha256",
        "decision_json",
        "decision_sha256",
        "checkpoint",
        "result_json",
        "created_at",
        "updated_at",
    }

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._connection:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS admission_decision_intents_v1 (
                    intent_id TEXT PRIMARY KEY,
                    ingress_request_id TEXT NOT NULL,
                    command_id TEXT NOT NULL UNIQUE,
                    correlation_id TEXT NOT NULL,
                    owner_domain_id TEXT NOT NULL,
                    business_owner_id TEXT NOT NULL,
                    enrollment_id TEXT NOT NULL,
                    actor_json TEXT NOT NULL,
                    actor_sha256 TEXT NOT NULL,
                    decision_json TEXT NOT NULL,
                    decision_sha256 TEXT NOT NULL,
                    checkpoint TEXT NOT NULL CHECK (
                        checkpoint IN ('intent_recorded', 'decision_committed')
                    ),
                    result_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(owner_domain_id, ingress_request_id)
                )
                """
            )
        actual = {
            row["name"]
            for row in self._connection.execute(
                "PRAGMA table_info(admission_decision_intents_v1)"
            )
        }
        if actual != self._COLUMNS:
            raise RuntimeError("Admin Admission Decision intent schema is unknown")

    def close(self) -> None:
        self._connection.close()

    def get_or_create(self, **values) -> AdmissionDecisionIntent:
        candidate = _new_intent(**values)
        with self._lock, self._connection:
            row = self._connection.execute(
                """SELECT * FROM admission_decision_intents_v1
                   WHERE owner_domain_id = ? AND ingress_request_id = ?""",
                (candidate.owner_domain_id, candidate.ingress_request_id),
            ).fetchone()
            if row is not None:
                existing = self._decode(row)
                _assert_same_intent(existing, candidate)
                return existing
            self._connection.execute(
                """INSERT INTO admission_decision_intents_v1 (
                       intent_id, ingress_request_id, command_id, correlation_id,
                       owner_domain_id, business_owner_id, enrollment_id,
                       actor_json, actor_sha256, decision_json, decision_sha256,
                       checkpoint, result_json, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)""",
                (
                    candidate.intent_id,
                    candidate.ingress_request_id,
                    candidate.command_id,
                    candidate.correlation_id,
                    candidate.owner_domain_id,
                    candidate.business_owner_id,
                    candidate.enrollment_id,
                    candidate.actor_json,
                    candidate.actor_sha256,
                    candidate.decision_json,
                    candidate.decision_sha256,
                    candidate.checkpoint,
                    candidate.created_at.isoformat(),
                    candidate.updated_at.isoformat(),
                ),
            )
        return candidate

    def mark_decision_committed(self, *, intent_id, result, now):
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT * FROM admission_decision_intents_v1 WHERE intent_id = ?",
                (intent_id,),
            ).fetchone()
            if row is None:
                raise ValueError("Admission Decision intent does not exist")
            intent = self._decode(row)
            if intent.result is not None and intent.result != result:
                raise ValueError("Hub Decision result changed for an existing intent")
            committed = _committed(intent, result=result, now=now)
            self._connection.execute(
                """UPDATE admission_decision_intents_v1
                   SET checkpoint = ?, result_json = ?, updated_at = ?
                   WHERE intent_id = ?""",
                (
                    committed.checkpoint,
                    _canonical(result),
                    committed.updated_at.isoformat(),
                    intent_id,
                ),
            )
            return committed

    @staticmethod
    def _decode(row: sqlite3.Row) -> AdmissionDecisionIntent:
        result_json = row["result_json"]
        return AdmissionDecisionIntent(
            intent_id=row["intent_id"],
            ingress_request_id=row["ingress_request_id"],
            command_id=row["command_id"],
            correlation_id=row["correlation_id"],
            owner_domain_id=row["owner_domain_id"],
            business_owner_id=row["business_owner_id"],
            enrollment_id=row["enrollment_id"],
            actor_json=row["actor_json"],
            actor_sha256=row["actor_sha256"],
            decision_json=row["decision_json"],
            decision_sha256=row["decision_sha256"],
            checkpoint=row["checkpoint"],
            result=(
                DecideEnrollmentResult.model_validate_json(result_json)
                if result_json is not None
                else None
            ),
            created_at=datetime.fromisoformat(row["created_at"]).astimezone(UTC),
            updated_at=datetime.fromisoformat(row["updated_at"]).astimezone(UTC),
        )


def _new_intent(
    *,
    ingress_request_id: str,
    owner_domain_id: str,
    business_owner_id: str,
    actor: ControllerActorRef,
    decision: DecideEnrollment,
    now: datetime,
) -> AdmissionDecisionIntent:
    intent_id = decision_intent_id(
        owner_domain_id=owner_domain_id, request_id=ingress_request_id
    )
    actor_json = _canonical(actor)
    decision_json = _canonical(decision)
    now = now.astimezone(UTC)
    return AdmissionDecisionIntent(
        intent_id=intent_id,
        ingress_request_id=ingress_request_id,
        command_id=decision_command_id(
            intent_id=intent_id, enrollment_id=decision.enrollment_id
        ),
        correlation_id=intent_id,
        owner_domain_id=owner_domain_id,
        business_owner_id=business_owner_id,
        enrollment_id=decision.enrollment_id,
        actor_json=actor_json,
        actor_sha256=_digest(actor_json),
        decision_json=decision_json,
        decision_sha256=_digest(decision_json),
        checkpoint="intent_recorded",
        result=None,
        created_at=now,
        updated_at=now,
    )


def _assert_same_intent(
    existing: AdmissionDecisionIntent, candidate: AdmissionDecisionIntent
) -> None:
    if (
        existing.business_owner_id != candidate.business_owner_id
        or existing.enrollment_id != candidate.enrollment_id
        or existing.actor_sha256 != candidate.actor_sha256
        or existing.decision_sha256 != candidate.decision_sha256
        or existing.command_id != candidate.command_id
    ):
        raise ValueError("Admission Decision request id was reused with different context")


def _committed(
    intent: AdmissionDecisionIntent,
    *,
    result: DecideEnrollmentResult,
    now: datetime,
) -> AdmissionDecisionIntent:
    return replace(
        intent,
        checkpoint="decision_committed",
        result=result,
        updated_at=now.astimezone(UTC),
    )
