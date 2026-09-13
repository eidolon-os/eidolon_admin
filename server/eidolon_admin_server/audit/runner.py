"""Keeping the audit index up to date from inside the process that owns it.

The indexer and the store existed and were tested for a while, and nothing on a
Host ran either. So every authority's dispatcher published into a stream that did
not exist — the consumer is what created it — and the index stayed empty.

**It runs here rather than as its own service.** The index is Admin's own
rebuildable projection, and Admin is where it is read from; a separate unit would
mean a new entry in the reviewed product topology, which every operator's Host
config has to agree with, to run a loop next to the process that owns the file
anyway. The state directory it writes was already declared for this component.

**This is the only way to start it.** There was once a second one — a
``eidolon-audit-index`` console script driving a supervisor program — left over
from the separate-worker design. It was never included by any profile, and
keeping it meant a second process could be pointed at the same SQLite file,
which is precisely the single-writer property this arrangement exists to hold.
Both are gone; do not reintroduce an entry point without answering that.

**A bus that is down must not take Admin with it.** Failures are logged and
retried with a bounded backoff, never raised into the app: an audit projection
falling behind is a degraded read, while Admin refusing to start is every
Owner-facing surface at once. And falling behind costs nothing durable — the
authorities keep what they could not publish.

**But falling behind must be sayable.** Tolerating the failure and hiding it are
different things, and this file used to do both: the loop caught everything and
the only reader of the index had no way to ask whether it was being filled, so a
Host whose indexer had never consumed a single message rendered an events lane
that said ``ok`` with nothing in it. :class:`AuditIndexerHealth` is the answer
the lane was missing — an empty house and a broken camera no longer look alike.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from eidolon_sdk.integrations.audit import AUDIT_MAX_AGE

from .index import AuditIndexSettings, AuditIndexStore
from .jetstream import AuditJetStreamSettings, JetStreamAuditIndexer

logger = logging.getLogger(__name__)

_IDLE_SLEEP_SECONDS = 0.25
_FAILURE_BACKOFF_SECONDS = 5.0
_PRUNE_INTERVAL_SECONDS = 60 * 60

#: How much of this projection survives a prune, on top of the age bound.
#:
#: A ceiling for a producer that suddenly got loud, which an age bound reacts to
#: far too slowly. Chosen against the read it serves: the events lane asks for
#: the newest 120 rows, so this is three orders of magnitude of headroom over
#: what anyone actually reads, and still a bounded file.
INDEX_MAX_ROWS = 200_000

#: How far back the projection keeps rows.
#:
#: The stream's own window, deliberately: replaying a stream that holds thirty
#: days into an index that holds a year just produces a year of nothing. One
#: declaration, in the SDK, so the two cannot drift apart.
INDEX_RETENTION: timedelta = AUDIT_MAX_AGE


@dataclass
class AuditIndexerHealth:
    """Whether the loop filling the index is actually filling it.

    Read by the Owner's events lane, which has no other way to tell an index
    nobody is writing from a Host where nothing has happened. Both are an empty
    table; only one of them is worth telling somebody about.
    """

    #: False until the first poll comes back, so "started but never answered"
    #: — a wrong URL, a bus that was never there — is not read as healthy.
    ok: bool = False
    detail: str = "审计索引还没有开始消费：事件流未建立"

    def observed(self) -> None:
        self.ok = True
        self.detail = ""

    def failed(self, error: BaseException) -> None:
        self.ok = False
        self.detail = f"{type(error).__name__}: {error}"


async def run_audit_indexer(
    *,
    nats_url: str,
    sqlite_path: str,
    health: AuditIndexerHealth | None = None,
) -> None:
    """Consume the audit stream into the index until cancelled."""

    health = health or AuditIndexerHealth()
    index = AuditIndexStore.open(AuditIndexSettings(sqlite_path=sqlite_path))
    await index.init_schema()
    indexer = JetStreamAuditIndexer(index, AuditJetStreamSettings(url=nats_url))
    next_prune = 0.0
    try:
        while True:
            try:
                ingested = await indexer.consume_once()
                health.observed()
                now = asyncio.get_running_loop().time()
                if now >= next_prune:
                    await _prune_once(index)
                    next_prune = now + _PRUNE_INTERVAL_SECONDS
            except asyncio.CancelledError:
                raise
            except Exception as error:
                health.failed(error)
                logger.exception("audit indexer iteration failed")
                # Drop the connection, so the retry rebuilds the subscription
                # rather than asking a dead one again.
                #
                # ``connect()`` returns early while the TCP connection is alive,
                # and a pull subscription can die on its own: delete the stream
                # or its durable consumer — which recreating a stream does — and
                # every later fetch answers ServiceUnavailable forever while the
                # socket stays perfectly healthy. Observed exactly that: three
                # events sat in a stream nobody was consuming, and the loop
                # retried against the same dead consumer every five seconds
                # without ever trying to re-subscribe.
                with suppress(Exception):
                    await indexer.close()
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


async def _prune_once(index: AuditIndexStore) -> None:
    """Apply the projection's bounds, and reclaim the file only if it changed."""

    removed = await index.prune(
        before=datetime.now(UTC) - INDEX_RETENTION,
        max_rows=INDEX_MAX_ROWS,
    )
    if removed:
        # Rare by construction: hourly at most, and only when a prune actually
        # deleted something. VACUUM takes a write lock for as long as it runs.
        await index.reclaim()
        logger.info("audit index pruned %d row(s) and reclaimed its file", removed)


__all__ = [
    "INDEX_MAX_ROWS",
    "INDEX_RETENTION",
    "AuditIndexerHealth",
    "run_audit_indexer",
]
