"""Background dispatcher for the system-data authority's local audit outbox."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import UTC, datetime, timedelta

from eidolon_data import DataStore
from eidolon_data.audit import AuditOutboxDispatcher
from eidolon_sdk.integrations.audit import (
    AuditNatsPublisherSettings,
    JetStreamAuditPublisher,
)

logger = logging.getLogger(__name__)

_PUBLISHED_RETENTION = timedelta(hours=24)
_PURGE_INTERVAL_SECONDS = 60 * 60


async def run_system_data_audit_dispatcher(store: DataStore) -> None:
    """Drain only Data's local outbox; never open another authority database."""

    publisher = JetStreamAuditPublisher(
        AuditNatsPublisherSettings(
            url=os.environ.get("EIDOLON_NATS_URL", "nats://127.0.0.1:4222"),
        ),
        connection_name="eidolon-data-audit-publisher",
    )
    dispatcher = AuditOutboxDispatcher(store.audit_outbox, publisher)
    next_purge = 0.0
    try:
        while True:
            try:
                published = await dispatcher.dispatch_once()
                now = time.monotonic()
                if now >= next_purge:
                    purged = await store.audit_outbox.purge_published(
                        before=datetime.now(UTC) - _PUBLISHED_RETENTION
                    )
                    if purged:
                        logger.info(
                            "purged %d acknowledged system-data audit outbox rows",
                            purged,
                        )
                    next_purge = now + _PURGE_INTERVAL_SECONDS
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - worker must not kill Admin
                logger.exception("system-data audit dispatcher iteration failed")
                published = 0
            # Drain a backlog without a fixed delay; idle and failed rounds use
            # a small poll interval. Neither path runs in a request coroutine.
            if published == 0:
                await asyncio.sleep(0.25)
    finally:
        await publisher.close()
