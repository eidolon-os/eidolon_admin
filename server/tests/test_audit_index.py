from __future__ import annotations

from datetime import UTC, datetime

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
