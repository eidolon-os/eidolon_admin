from __future__ import annotations

import pytest
from eidolon_data import DataSettings, DataStore
from sqlalchemy import text

from eidolon_admin_server.app.data.schema_guard import ensure_eidolon_data_schema

pytestmark = pytest.mark.asyncio


async def test_schema_guard_repairs_known_additive_drift(tmp_path) -> None:
    store = DataStore.open(DataSettings(sqlite_path=str(tmp_path / "drift.sqlite3")))
    try:
        async with store.engine.begin() as conn:
            await conn.execute(
                text(
                    "CREATE TABLE companions ("
                    "companion_id VARCHAR(64) PRIMARY KEY, "
                    "is_master BOOLEAN NOT NULL DEFAULT 0"
                    ")"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO companions (companion_id, is_master) "
                    "VALUES ('c-master', 1)"
                )
            )
            await conn.execute(
                text(
                    "CREATE TABLE turns ("
                    "turn_id VARCHAR(64) PRIMARY KEY, "
                    "runtime_session_id VARCHAR(128)"
                    ")"
                )
            )
            await conn.execute(
                text(
                    "CREATE TABLE events ("
                    "event_id VARCHAR(64) PRIMARY KEY, "
                    "owner_id VARCHAR(64) NOT NULL, "
                    "created_at DATETIME NOT NULL"
                    ")"
                )
            )

        repaired = await ensure_eidolon_data_schema(store)

        async with store.engine.begin() as conn:
            turn_columns = {
                row[1]
                for row in (await conn.execute(text("PRAGMA table_info(turns)"))).fetchall()
            }
            companion_columns = {
                row[1]
                for row in (await conn.execute(text("PRAGMA table_info(companions)"))).fetchall()
            }
            event_columns = {
                row[1]
                for row in (await conn.execute(text("PRAGMA table_info(events)"))).fetchall()
            }
            turn_indexes = {
                row[1]
                for row in (await conn.execute(text("PRAGMA index_list(turns)"))).fetchall()
            }
            row = (
                await conn.execute(
                    text("SELECT companion_type FROM companions WHERE companion_id='c-master'")
                )
            ).one()

        assert "turns.trace_id" in repaired["columns"]
        assert "companions.companion_type" in repaired["columns"]
        assert "events.trace_id" in repaired["columns"]
        assert "trace_id" in turn_columns
        assert "companion_type" in companion_columns
        assert {"trace_id", "event_class", "source", "severity", "outcome"}.issubset(
            event_columns
        )
        assert "ix_turns_trace_id" in turn_indexes
        assert row[0] == "master"
    finally:
        await store.close()
