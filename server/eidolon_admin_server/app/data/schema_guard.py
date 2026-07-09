"""Runtime guard for local Eidolon Data schema drift.

Admin runs against the shared ``eidolon_data`` SQLite database.  In local dev,
older databases may have been created with ``Base.metadata.create_all()``, which
creates missing tables but never alters existing ones.  If Alembic is later
stamped forward, the revision can say "head" while a table is still missing a
new column.  Cross-project readers then fail with plain SQL 500s.

This guard is intentionally small and idempotent: it repairs only additive
columns/indexes that are safe for SQLite and already expected by current
runtime models.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from eidolon_data import DataStore
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ColumnRepair:
    table: str
    column: str
    ddl: str


@dataclass(frozen=True)
class IndexRepair:
    table: str
    index: str
    columns: tuple[str, ...]


_COLUMN_REPAIRS: tuple[ColumnRepair, ...] = (
    ColumnRepair("companions", "is_master", "is_master BOOLEAN NOT NULL DEFAULT 0"),
    ColumnRepair(
        "companions",
        "companion_type",
        "companion_type VARCHAR(16) NOT NULL DEFAULT 'slave'",
    ),
    ColumnRepair(
        "conversations",
        "runtime_session_id",
        "runtime_session_id VARCHAR(128) REFERENCES runtime_sessions(session_id) ON DELETE SET NULL",
    ),
    ColumnRepair(
        "turns",
        "runtime_session_id",
        "runtime_session_id VARCHAR(128) REFERENCES runtime_sessions(session_id) ON DELETE SET NULL",
    ),
    ColumnRepair("turns", "trace_id", "trace_id VARCHAR(64)"),
    ColumnRepair(
        "persona_genomes",
        "schema_version",
        "schema_version VARCHAR(64) NOT NULL DEFAULT 'eidolon.persona_genome.v1'",
    ),
    ColumnRepair("persona_genomes", "genome_hash", "genome_hash VARCHAR(80) NOT NULL DEFAULT ''"),
    ColumnRepair(
        "persona_genomes",
        "compiler_version",
        "compiler_version VARCHAR(64) NOT NULL DEFAULT 'eidolon.persona_compiler.v1'",
    ),
    ColumnRepair("persona_genomes", "stable_prompt_hash", "stable_prompt_hash VARCHAR(80)"),
    ColumnRepair("persona_genomes", "applied_event_id", "applied_event_id VARCHAR(64)"),
    ColumnRepair("events", "companion_id", "companion_id VARCHAR(64)"),
    ColumnRepair("events", "event_class", "event_class VARCHAR(8) NOT NULL DEFAULT 'audit'"),
    ColumnRepair("events", "source", "source VARCHAR(16) NOT NULL DEFAULT 'data'"),
    ColumnRepair("events", "severity", "severity VARCHAR(8) NOT NULL DEFAULT 'info'"),
    ColumnRepair("events", "outcome", "outcome VARCHAR(12) NOT NULL DEFAULT 'success'"),
    ColumnRepair("events", "reason", "reason VARCHAR(256)"),
    ColumnRepair("events", "trace_id", "trace_id VARCHAR(64)"),
    ColumnRepair(
        "events",
        "data_classification",
        "data_classification VARCHAR(10) NOT NULL DEFAULT 'safe'",
    ),
    ColumnRepair("events", "schema_version", "schema_version INTEGER NOT NULL DEFAULT 1"),
    ColumnRepair("events", "occurred_at", "occurred_at DATETIME"),
)

_INDEX_REPAIRS: tuple[IndexRepair, ...] = (
    IndexRepair("companions", "ix_companions_is_master", ("is_master",)),
    IndexRepair("companions", "ix_companions_companion_type", ("companion_type",)),
    IndexRepair("conversations", "ix_conversations_runtime_session_id", ("runtime_session_id",)),
    IndexRepair("turns", "ix_turns_runtime_session_id", ("runtime_session_id",)),
    IndexRepair("turns", "ix_turns_trace_id", ("trace_id",)),
    IndexRepair("persona_genomes", "ix_persona_genomes_schema_version", ("schema_version",)),
    IndexRepair("persona_genomes", "ix_persona_genomes_genome_hash", ("genome_hash",)),
    IndexRepair("persona_genomes", "ix_persona_genomes_stable_prompt_hash", ("stable_prompt_hash",)),
    IndexRepair("persona_genomes", "ix_persona_genomes_applied_event_id", ("applied_event_id",)),
    IndexRepair("events", "ix_events_companion_id", ("companion_id",)),
    IndexRepair("events", "ix_events_event_class", ("event_class",)),
    IndexRepair("events", "ix_events_source", ("source",)),
    IndexRepair("events", "ix_events_severity", ("severity",)),
    IndexRepair("events", "ix_events_outcome", ("outcome",)),
    IndexRepair("events", "ix_events_trace_id", ("trace_id",)),
    IndexRepair("events", "ix_events_owner_created", ("owner_id", "created_at")),
)


async def ensure_eidolon_data_schema(store: DataStore) -> dict[str, list[str]]:
    """Repair known additive schema drift and verify required columns exist."""
    repaired_columns: list[str] = []
    repaired_indexes: list[str] = []
    async with store.engine.begin() as conn:
        tables = await _tables(conn)
        columns_by_table: dict[str, set[str]] = {}

        for repair in _COLUMN_REPAIRS:
            if repair.table not in tables:
                continue
            columns = columns_by_table.setdefault(repair.table, await _columns(conn, repair.table))
            if repair.column in columns:
                continue
            await conn.execute(text(f"ALTER TABLE {repair.table} ADD COLUMN {repair.ddl}"))
            columns.add(repair.column)
            repaired_columns.append(f"{repair.table}.{repair.column}")

        await _backfill_safe_defaults(conn, columns_by_table)

        for repair in _INDEX_REPAIRS:
            if repair.table not in tables:
                continue
            columns = columns_by_table.setdefault(repair.table, await _columns(conn, repair.table))
            if not set(repair.columns).issubset(columns):
                continue
            indexes = await _indexes(conn, repair.table)
            if repair.index in indexes:
                continue
            quoted_columns = ", ".join(repair.columns)
            await conn.execute(
                text(f"CREATE INDEX IF NOT EXISTS {repair.index} ON {repair.table} ({quoted_columns})")
            )
            repaired_indexes.append(f"{repair.table}.{repair.index}")

        await _assert_required_columns(conn)

    if repaired_columns or repaired_indexes:
        logger.warning(
            "eidolon_data schema drift repaired columns=%s indexes=%s",
            repaired_columns,
            repaired_indexes,
        )
    return {"columns": repaired_columns, "indexes": repaired_indexes}


async def _tables(conn: AsyncConnection) -> set[str]:
    result = await conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    )
    return {str(row[0]) for row in result.fetchall()}


async def _columns(conn: AsyncConnection, table: str) -> set[str]:
    result = await conn.execute(text(f"PRAGMA table_info({table})"))
    return {str(row[1]) for row in result.fetchall()}


async def _indexes(conn: AsyncConnection, table: str) -> set[str]:
    result = await conn.execute(text(f"PRAGMA index_list({table})"))
    return {str(row[1]) for row in result.fetchall()}


async def _backfill_safe_defaults(
    conn: AsyncConnection,
    columns_by_table: dict[str, set[str]],
) -> None:
    companion_columns = columns_by_table.get("companions") or await _columns(conn, "companions")
    if {"companion_type", "is_master"}.issubset(companion_columns):
        await conn.execute(
            text(
                "UPDATE companions "
                "SET companion_type = 'master' "
                "WHERE is_master = 1 AND companion_type != 'master'"
            )
        )
        await conn.execute(
            text(
                "UPDATE companions SET companion_type = 'slave' "
                "WHERE companion_type IS NULL OR companion_type = ''"
            )
        )

    event_columns = columns_by_table.get("events") or await _columns(conn, "events")
    if {"occurred_at", "created_at"}.issubset(event_columns):
        await conn.execute(
            text("UPDATE events SET occurred_at = created_at WHERE occurred_at IS NULL")
        )


async def _assert_required_columns(conn: AsyncConnection) -> None:
    required: dict[str, set[str]] = {
        "companions": {"is_master", "companion_type"},
        "persona_genomes": {
            "schema_version",
            "genome_hash",
            "compiler_version",
            "stable_prompt_hash",
            "applied_event_id",
        },
        "turns": {"trace_id", "runtime_session_id"},
        "events": {"trace_id", "event_class", "source", "severity", "outcome"},
    }
    missing: list[str] = []
    tables = await _tables(conn)
    for table, columns in required.items():
        if table not in tables:
            continue
        existing = await _columns(conn, table)
        missing.extend(f"{table}.{column}" for column in sorted(columns - existing))
    if missing:
        raise RuntimeError(f"eidolon_data schema is missing required columns: {', '.join(missing)}")
