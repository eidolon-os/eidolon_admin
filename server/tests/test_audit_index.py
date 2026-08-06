from __future__ import annotations

from datetime import UTC, datetime

from eidolon_admin_server.audit import AuditIndexSettings, AuditIndexStore
from eidolon_sdk.biz.audit import AuditEnvelope


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
