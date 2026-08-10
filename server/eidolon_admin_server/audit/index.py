"""Independent, rebuildable SQLite projection of the global audit stream."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from eidolon_sdk.biz.audit import AuditEnvelope
from sqlalchemy import JSON, DateTime, Index, Integer, String, event, select, text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _default_audit_index_path() -> str:
    root = Path(os.environ.get("EIDOLON_STATE_ROOT", "~/eidolon/data")).expanduser()
    return str(root / "audit/audit-index.sqlite3")


class _AuditIndexBase(DeclarativeBase):
    pass


class _AuditIndexRow(_AuditIndexBase):
    __tablename__ = "audit_events"

    ingest_seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(64), unique=True)
    producer: Mapped[str] = mapped_column(String(64))
    producer_seq: Mapped[int] = mapped_column(Integer)
    category: Mapped[str] = mapped_column(String(16))
    owner_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    subject_type: Mapped[str] = mapped_column(String(64))
    subject_id: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(128))
    outcome: Mapped[str] = mapped_column(String(16))
    severity: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str | None] = mapped_column(String(256), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    data_classification: Mapped[str] = mapped_column(String(16))
    schema_version: Mapped[int] = mapped_column(Integer)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP")
    )

    __table_args__ = (
        Index("ix_audit_events_owner_occurred", "owner_id", "occurred_at"),
        Index("ix_audit_events_trace_occurred", "trace_id", "occurred_at"),
        Index(
            "ix_audit_events_subject_occurred",
            "subject_type",
            "subject_id",
            "occurred_at",
        ),
    )


@dataclass(frozen=True)
class AuditIndexSettings:
    sqlite_path: str = field(default_factory=_default_audit_index_path)
    busy_timeout_ms: int = 5_000
    wal_autocheckpoint_pages: int = 1_000
    read_only: bool = False


class AuditIndexStore:
    def __init__(
        self,
        engine: AsyncEngine,
        session_factory: async_sessionmaker,
        *,
        read_only: bool,
    ) -> None:
        self.engine = engine
        self._session_factory = session_factory
        self.read_only = read_only

    @classmethod
    def open(cls, settings: AuditIndexSettings | None = None) -> AuditIndexStore:
        resolved = settings or AuditIndexSettings()
        path = Path(resolved.sqlite_path).expanduser()
        if not resolved.read_only:
            path.parent.mkdir(parents=True, exist_ok=True)
        database_url = (
            f"sqlite+aiosqlite:///file:{path}?mode=ro&uri=true"
            if resolved.read_only
            else f"sqlite+aiosqlite:///{path}"
        )
        engine = create_async_engine(
            database_url,
            connect_args={"timeout": resolved.busy_timeout_ms / 1_000},
            pool_size=1,
            max_overflow=0,
        )

        @event.listens_for(engine.sync_engine, "connect")
        def _configure(connection, _record) -> None:  # type: ignore[no-untyped-def]
            cursor = connection.cursor()
            try:
                cursor.execute(f"PRAGMA busy_timeout={resolved.busy_timeout_ms}")
                if resolved.read_only:
                    cursor.execute("PRAGMA query_only=ON")
                else:
                    cursor.execute("PRAGMA journal_mode=WAL")
                    cursor.execute("PRAGMA synchronous=NORMAL")
                    cursor.execute(
                        "PRAGMA wal_autocheckpoint="
                        f"{resolved.wal_autocheckpoint_pages}"
                    )
            finally:
                cursor.close()

        return cls(
            engine,
            async_sessionmaker(engine, expire_on_commit=False),
            read_only=resolved.read_only,
        )

    async def init_schema(self) -> None:
        if self.read_only:
            raise RuntimeError("read-only audit index clients cannot initialize schema")
        async with self.engine.begin() as connection:
            await connection.run_sync(_AuditIndexBase.metadata.create_all)

    async def validate_schema(self) -> None:
        """Verify the projection exists and read clients are technically read-only."""
        async with self.engine.connect() as connection:
            table = await connection.scalar(
                text(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name='audit_events'"
                )
            )
            if table != "audit_events":
                raise RuntimeError("audit index schema is unavailable")
            if self.read_only:
                query_only = int(
                    (await connection.execute(text("PRAGMA query_only"))).scalar_one()
                )
                if query_only != 1:
                    raise RuntimeError("audit index reader is not query-only")

    async def close(self) -> None:
        await self.engine.dispose()

    async def ingest(self, events: list[AuditEnvelope]) -> int:
        if self.read_only:
            raise RuntimeError("read-only audit index clients cannot ingest")
        if not events:
            return 0
        values = [
            {
                "event_id": item.event_id,
                "producer": item.producer,
                "producer_seq": item.producer_seq,
                "category": item.category,
                "owner_id": item.owner_id,
                "subject_type": item.subject_type,
                "subject_id": item.subject_id,
                "action": item.action,
                "outcome": item.outcome,
                "severity": item.severity,
                "reason": item.reason,
                "trace_id": item.trace_id,
                "data_classification": item.data_classification,
                "schema_version": item.schema_version,
                "payload_json": item.payload,
                "occurred_at": item.occurred_at,
            }
            for item in events
        ]
        async with self._session_factory() as session:
            result = await session.execute(
                sqlite_insert(_AuditIndexRow)
                .values(values)
                .on_conflict_do_nothing(index_elements=["event_id"])
            )
            await session.commit()
            return int(result.rowcount or 0)

    async def list_for_owner(self, owner_id: str, *, limit: int = 200) -> list[AuditEnvelope]:
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(_AuditIndexRow)
                .where(_AuditIndexRow.owner_id == owner_id)
                .order_by(_AuditIndexRow.occurred_at.desc(), _AuditIndexRow.ingest_seq.desc())
                .limit(limit)
            )
            return [_index_envelope(row) for row in rows]

    async def list_for_owner_since(
        self,
        owner_id: str,
        *,
        after: datetime,
        limit: int = 200,
    ) -> list[AuditEnvelope]:
        """Return a bounded tail; callers deduplicate equal-timestamp rows by id."""
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(_AuditIndexRow)
                .where(_AuditIndexRow.owner_id == owner_id)
                .where(_AuditIndexRow.occurred_at >= after)
                .order_by(_AuditIndexRow.occurred_at, _AuditIndexRow.ingest_seq)
                .limit(limit)
            )
            return [_index_envelope(row) for row in rows]


def _index_envelope(row: _AuditIndexRow) -> AuditEnvelope:
    return AuditEnvelope(
        event_id=row.event_id,
        producer=row.producer,
        producer_seq=row.producer_seq,
        category=row.category,
        owner_id=row.owner_id,
        subject_type=row.subject_type,
        subject_id=row.subject_id,
        action=row.action,
        outcome=row.outcome,
        severity=row.severity,
        reason=row.reason,
        trace_id=row.trace_id,
        data_classification=row.data_classification,
        schema_version=row.schema_version,
        payload=dict(row.payload_json or {}),
        occurred_at=row.occurred_at,
    )
