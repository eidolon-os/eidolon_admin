from __future__ import annotations

import sqlite3
import hashlib
from datetime import UTC, datetime, timedelta

import pytest

from eidolon_admin_server.app.control_plane.contracts import (
    DeviceRef,
    HubClaimRevocationResult,
)
from eidolon_admin_server.app.control_plane.removal_intents import (
    SqliteRemovalIntentStore,
)


NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)


def _ref(*, owner_generation: int = 1, generation: int = 1) -> DeviceRef:
    return DeviceRef(
        device_instance_id="device-1",
        owner_domain_id="owner-1",
        owner_domain_generation=owner_generation,
        claim_generation=generation,
        trust_epoch=1,
    )


def _create(store: SqliteRemovalIntentStore, **overrides):
    context_json = overrides.get(
        "authorization_context_json", '{"audience":"eidolon-admission"}'
    )
    values = {
        "ingress_request_id": "mobile-removal-1",
        "owner_domain_id": "owner-1",
        "device_ref": _ref(),
        "actor_controller_id": "controller-1",
        "workload_principal_id": "eidolon-local-api",
        "controller_reset_epoch": 7,
        "authorization_context_json": context_json,
        "authorization_context_sha256": hashlib.sha256(context_json.encode()).hexdigest(),
        "reason": "owner-removed",
        "now": NOW,
        **overrides,
    }
    return store.get_or_create(**values)


def test_restart_replays_the_frozen_intent_and_hub_result(tmp_path) -> None:
    path = tmp_path / "removal-intents.sqlite3"
    first_store = SqliteRemovalIntentStore(path)
    intent = _create(first_store)
    result = HubClaimRevocationResult(
        operation="device.claim-revocation-result",
        command_id=intent.hub_command_id,
        outcome="committed",
        device_ref=intent.device_ref,
        aggregate_revision=3,
        occurred_at=NOW,
        event_id="claim-event-1",
        lifecycle_state="revoked",
    )
    first_store.mark_hub_committed(
        intent_id=intent.intent_id,
        result=result,
        now=NOW + timedelta(seconds=1),
    )
    first_store.close()

    restarted = SqliteRemovalIntentStore(path)
    try:
        replay = _create(restarted, now=NOW + timedelta(minutes=1))
    finally:
        restarted.close()

    assert replay.intent_id == intent.intent_id
    assert replay.device_ref == _ref()
    assert replay.hub_result == result


@pytest.mark.parametrize(
    "override",
    (
        {"device_ref": _ref(owner_generation=2)},
        {"device_ref": _ref(generation=2)},
        {"actor_controller_id": "controller-2"},
        {"reason": "different-reason"},
        {"workload_principal_id": "forged-local"},
        {"controller_reset_epoch": 8},
        {"authorization_context_json": '{"audience":"other"}'},
    ),
)
def test_request_id_cannot_drift_across_generation_actor_or_content(
    tmp_path, override
) -> None:
    store = SqliteRemovalIntentStore(tmp_path / "removal-intents.sqlite3")
    try:
        _create(store)
        with pytest.raises(ValueError, match="different removal content"):
            _create(store, **override)
    finally:
        store.close()


def test_unknown_existing_schema_is_rejected(tmp_path) -> None:
    path = tmp_path / "removal-intents.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE removal_intents(intent_id TEXT PRIMARY KEY)")
    connection.close()

    with pytest.raises(RuntimeError, match="schema version is unknown"):
        SqliteRemovalIntentStore(path)


def test_v3_database_expands_owner_generation_without_losing_intent(
    tmp_path,
) -> None:
    path = tmp_path / "removal-intents.sqlite3"
    store = SqliteRemovalIntentStore(path)
    original = _create(store)
    store.close()

    connection = sqlite3.connect(path)
    connection.execute(
        "UPDATE removal_intents SET device_ref_json = "
        "json_remove(device_ref_json, '$.owner_domain_generation')"
    )
    connection.execute(
        "ALTER TABLE removal_intents DROP COLUMN owner_domain_generation"
    )
    connection.execute("PRAGMA user_version=3")
    connection.close()

    migrated = SqliteRemovalIntentStore(path)
    try:
        replay = _create(migrated, now=NOW + timedelta(minutes=1))
        version = migrated._connection.execute("PRAGMA user_version").fetchone()[0]
    finally:
        migrated.close()

    assert version == 4
    assert replay.intent_id == original.intent_id
    assert replay.device_ref.owner_domain_generation == 1
