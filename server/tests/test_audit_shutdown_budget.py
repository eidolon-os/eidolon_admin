import asyncio
from unittest.mock import AsyncMock

import pytest

from eidolon_admin_server.audit.jetstream import AuditJetStreamSettings, JetStreamAuditIndexer


@pytest.mark.asyncio
async def test_stalled_drain_closes_connection_within_shutdown_budget():
    async def stalled():
        await asyncio.Event().wait()
    connection = AsyncMock()
    connection.drain.side_effect = stalled
    indexer = JetStreamAuditIndexer(None, AuditJetStreamSettings(drain_timeout_seconds=0.01))
    indexer._connection = connection
    await asyncio.wait_for(indexer.close(), 0.5)
    connection.close.assert_awaited_once()
    assert indexer._connection is None
    await indexer.close()
    connection.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancellation_during_drain_still_closes_connection():
    connection = AsyncMock()
    connection.drain.side_effect = asyncio.CancelledError
    indexer = JetStreamAuditIndexer(None, AuditJetStreamSettings())
    indexer._connection = connection
    with pytest.raises(asyncio.CancelledError):
        await indexer.close()
    connection.close.assert_awaited_once()
