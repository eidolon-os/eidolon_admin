"""Keeping the audit index up to date from inside the process that owns it.

The indexer, the store and its CLI have existed and been tested for a while, and
nothing on a Host ran any of them. So every authority's dispatcher published
into a stream that did not exist — the consumer is what creates it — and the
index stayed empty.

**It runs here rather than as its own service.** The index is Admin's own
rebuildable projection, and Admin is where it is read from; a separate unit would
mean a new entry in the reviewed product topology, which every operator's Host
config has to agree with, to run a loop next to the process that owns the file
anyway. The state directory it writes was already declared for this component.

**A bus that is down must not take Admin with it.** Failures are logged and
retried with a bounded backoff, never raised into the app: an audit projection
falling behind is a degraded read, while Admin refusing to start is every
Owner-facing surface at once. And falling behind costs nothing durable — the
authorities keep what they could not publish.
"""

from __future__ import annotations

import asyncio
import logging

from .index import AuditIndexSettings, AuditIndexStore
from .jetstream import AuditJetStreamSettings, JetStreamAuditIndexer

logger = logging.getLogger(__name__)

_IDLE_SLEEP_SECONDS = 0.25
_FAILURE_BACKOFF_SECONDS = 5.0


async def run_audit_indexer(
    *,
    nats_url: str,
    sqlite_path: str,
) -> None:
    """Consume the audit stream into the index until cancelled."""

    index = AuditIndexStore.open(AuditIndexSettings(sqlite_path=sqlite_path))
    await index.init_schema()
    indexer = JetStreamAuditIndexer(
        index, AuditJetStreamSettings(url=nats_url)
    )
    try:
        while True:
            try:
                ingested = await indexer.consume_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("audit indexer iteration failed")
                # Long enough that a bus which is simply not there does not turn
                # into a hot loop, short enough that a restarted one is picked up
                # without anyone intervening.
                await asyncio.sleep(_FAILURE_BACKOFF_SECONDS)
                continue
            if ingested == 0:
                await asyncio.sleep(_IDLE_SLEEP_SECONDS)
    finally:
        await indexer.close()
        await index.close()


__all__ = ["run_audit_indexer"]
