from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from eidolon_admin_server.app.data import audit_dispatch


async def test_data_dispatcher_purges_only_through_published_outbox_api(
    monkeypatch,
) -> None:
    purged_before: list[datetime] = []
    publisher_closed = False

    class _Outbox:
        async def purge_published(self, *, before: datetime) -> int:
            purged_before.append(before)
            return 0

    class _Store:
        audit_outbox = _Outbox()

    class _Publisher:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def close(self) -> None:
            nonlocal publisher_closed
            publisher_closed = True

    class _Dispatcher:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def dispatch_once(self) -> int:
            return 0

    async def _cancel_on_idle(_delay: float) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(audit_dispatch, "JetStreamAuditPublisher", _Publisher)
    monkeypatch.setattr(audit_dispatch, "AuditOutboxDispatcher", _Dispatcher)
    monkeypatch.setattr(audit_dispatch.asyncio, "sleep", _cancel_on_idle)

    started_at = datetime.now(UTC)
    with pytest.raises(asyncio.CancelledError):
        await audit_dispatch.run_system_data_audit_dispatcher(_Store())  # type: ignore[arg-type]

    assert publisher_closed is True
    assert len(purged_before) == 1
    expected = started_at - timedelta(hours=24)
    assert abs((purged_before[0] - expected).total_seconds()) < 1
