from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from eidolon_admin_server.audit import AuditIndexSettings, AuditIndexStore
from eidolon_sdk.biz.audit import AuditEnvelope
from sqlalchemy import text
from sqlalchemy.exc import OperationalError


async def test_audit_index_is_independent_idempotent_projection(tmp_path) -> None:
    index = AuditIndexStore.open(
        AuditIndexSettings(sqlite_path=str(tmp_path / "audit-index.sqlite3"))
    )
    await index.init_schema()
    event = AuditEnvelope(
        event_id="audit-index-1",
        producer="eidolon-kernel",
        producer_seq=7,
        category="governance",
        owner_id="owner-1",
        subject_type="device_mount",
        subject_id="device-1",
        action="device.mount.created",
        occurred_at=datetime.now(UTC),
    )
    try:
        assert await index.ingest([event]) == 1
        assert await index.ingest([event]) == 0
        rows = await index.list_for_owner("owner-1")
        assert [row.event_id for row in rows] == ["audit-index-1"]
    finally:
        await index.close()


async def test_audit_index_reader_is_query_only(tmp_path) -> None:
    path = tmp_path / "audit-index.sqlite3"
    writer = AuditIndexStore.open(AuditIndexSettings(sqlite_path=str(path)))
    await writer.init_schema()
    event = AuditEnvelope(
        event_id="audit-reader-1",
        producer="eidolon-data",
        producer_seq=1,
        category="governance",
        owner_id="owner-reader",
        subject_type="owner",
        subject_id="owner-reader",
        action="owner.created",
        occurred_at=datetime.now(UTC),
    )
    await writer.ingest([event])
    await writer.close()

    reader = AuditIndexStore.open(
        AuditIndexSettings(sqlite_path=str(path), read_only=True)
    )
    try:
        await reader.validate_schema()
        assert [row.event_id for row in await reader.list_for_owner("owner-reader")] == [
            "audit-reader-1"
        ]
        with pytest.raises(RuntimeError, match="cannot ingest"):
            await reader.ingest([event])
        with pytest.raises(OperationalError):
            async with reader.engine.begin() as connection:
                await connection.execute(text("DELETE FROM audit_events"))
    finally:
        await reader.close()


async def test_the_indexer_only_runs_when_a_stream_is_configured(tmp_path) -> None:
    """Where the loop is decided, asserted rather than trusted.

    The index is Admin's own projection, so the loop that fills it lives in
    Admin rather than in a unit of its own — a separate service would mean a new
    entry in the reviewed product topology that every operator's Host config has
    to agree with, to run a loop beside the process that owns the file anyway.

    With no URL there is no indexer. That also means no authority publishes,
    because the consumer is what creates the stream — and nothing is lost by
    that: they keep what they could not send.
    """

    import asyncio

    from eidolon_admin_server.app.main import create_app
    from eidolon_admin_server.app.settings import Settings

    settings = Settings(
        state_dir=tmp_path / "admin",
        services_file=Path(__file__).resolve().parents[2] / "config/services.yaml",
    )
    assert settings.audit_nats_url == ""

    app = create_app(settings=settings)
    async with app.router.lifespan_context(app):
        running = [
            task
            for task in asyncio.all_tasks()
            if task.get_name() == "eidolon-admin-audit-indexer"
        ]
        assert running == []


def _event(seq: int, *, occurred_at: datetime) -> AuditEnvelope:
    return AuditEnvelope(
        event_id=f"audit-retention-{seq}",
        producer="eidolon-system-data",
        producer_seq=seq,
        category="governance",
        owner_id="owner-1",
        subject_type="companion",
        subject_id="cmp-1",
        action="companion.archived",
        occurred_at=occurred_at,
    )


async def test_the_projection_keeps_a_window_not_a_history(tmp_path) -> None:
    """Unbounded growth inside a hardened unit's state directory.

    The index had no retention at all. It is a projection of a stream that keeps
    thirty days, serving a lane that reads the newest 120 rows — so anything
    older than the stream can never be rebuilt and is never read.
    """

    index = AuditIndexStore.open(
        AuditIndexSettings(sqlite_path=str(tmp_path / "audit-index.sqlite3"))
    )
    await index.init_schema()
    now = datetime.now(UTC)
    try:
        await index.ingest(
            [
                _event(1, occurred_at=now - timedelta(days=40)),
                _event(2, occurred_at=now - timedelta(days=31)),
                _event(3, occurred_at=now - timedelta(days=2)),
            ]
        )

        assert await index.prune(before=now - timedelta(days=30), max_rows=1_000) == 2

        rows = await index.list_for_owner("owner-1")
        assert [row.event_id for row in rows] == ["audit-retention-3"]
    finally:
        await index.close()


async def test_a_producer_that_got_loud_hits_a_row_bound_too(tmp_path) -> None:
    """An age bound reacts far too slowly to a producer that suddenly floods."""

    index = AuditIndexStore.open(
        AuditIndexSettings(sqlite_path=str(tmp_path / "audit-index.sqlite3"))
    )
    await index.init_schema()
    now = datetime.now(UTC)
    try:
        await index.ingest([_event(seq, occurred_at=now) for seq in range(1, 11)])

        # All ten are well inside the age window; only the bound removes them.
        assert await index.prune(before=now - timedelta(days=30), max_rows=4) == 6

        rows = await index.tail_for_owner("owner-1", limit=100)
        # The newest four, and the order this index assigned them is intact —
        # a reader's cursor still means what it meant.
        assert [row.envelope.producer_seq for row in rows] == [7, 8, 9, 10]
        assert [row.ingest_seq for row in rows] == sorted(r.ingest_seq for r in rows)
    finally:
        await index.close()


async def test_pruning_gives_the_pages_back_to_the_filesystem(tmp_path) -> None:
    """DELETE frees pages *inside* the database; only VACUUM returns them.

    Measured in pages rather than bytes on disk: this index runs in WAL mode, so
    right after a write the bytes are in the ``-wal`` sidecar and the main file's
    size says nothing. ``page_count`` is the size that actually grows without
    bound, which is what a retention policy is for.
    """

    path = tmp_path / "audit-index.sqlite3"
    index = AuditIndexStore.open(AuditIndexSettings(sqlite_path=str(path)))
    await index.init_schema()
    now = datetime.now(UTC)

    async def _pages() -> tuple[int, int]:
        async with index.engine.connect() as connection:
            total = await connection.scalar(text("PRAGMA page_count"))
            free = await connection.scalar(text("PRAGMA freelist_count"))
            return int(total), int(free)

    try:
        await index.ingest([_event(seq, occurred_at=now) for seq in range(1, 501)])
        await index.prune(before=now - timedelta(days=30), max_rows=10)
        grown, freed = await _pages()
        # The rows are gone and the pages they used are not.
        assert freed > 0

        await index.reclaim()

        reclaimed, still_free = await _pages()
        assert reclaimed < grown
        assert still_free == 0
    finally:
        await index.close()


async def test_a_read_only_client_may_not_prune_or_vacuum(tmp_path) -> None:
    path = tmp_path / "audit-index.sqlite3"
    writer = AuditIndexStore.open(AuditIndexSettings(sqlite_path=str(path)))
    await writer.init_schema()
    await writer.close()
    reader = AuditIndexStore.open(
        AuditIndexSettings(sqlite_path=str(path), read_only=True)
    )
    try:
        with pytest.raises(RuntimeError):
            await reader.prune(before=datetime.now(UTC), max_rows=1)
        with pytest.raises(RuntimeError):
            await reader.reclaim()
    finally:
        await reader.close()
