"""Where the audit index lives, and what happens when it is not there yet.

Both halves of one real failure. The index path used to be a *sibling* of
Admin's state directory (``<root>/audit/``), and the Admin unit is hardened:
``ProtectSystem=strict`` with ``StateDirectory=eidolon/admin``, so
``/var/lib/eidolon/admin`` is the only path it may write. The indexer therefore
died on its first ``mkdir`` — and because nobody retrieves a background task's
exception, the process said nothing. No JetStream stream was ever created, no
index file ever existed, and the Owner's events lane read "this Host has no
audit index" with a cause nobody could see.

The other half was mine: the read handle was decided at startup by
``path.exists()``, in the same lifespan that starts the loop which creates the
file. That check can only ever lose, and having lost it was never revisited.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from eidolon_admin_server.app.main import LazyAuditIndex
from eidolon_admin_server.audit import default_audit_index_path

pytestmark = pytest.mark.asyncio


async def test_the_index_lives_inside_the_directory_the_unit_may_write(
    monkeypatch,
) -> None:
    monkeypatch.setenv("EIDOLON_STATE_ROOT", "/var/lib/eidolon")

    path = Path(default_audit_index_path())

    # StateDirectory=eidolon/admin grants exactly this subtree. A sibling of it
    # is not writable under ProtectSystem=strict, however plausible the path
    # looks in a repository.
    assert path.is_relative_to(Path("/var/lib/eidolon/admin"))
    assert path.name == "audit-index.sqlite3"


async def test_one_source_for_the_path(monkeypatch) -> None:
    """The expression had copies in several places; a move would strand them."""

    monkeypatch.setenv("EIDOLON_STATE_ROOT", "/tmp/eidolon-state-test")
    from eidolon_admin_server.audit import index

    assert index.AuditIndexSettings().sqlite_path == default_audit_index_path()
    # And nothing recomputes it by hand.
    source = (Path(index.__file__).parent.parent / "app" / "main.py").read_text(
        encoding="utf-8"
    )
    assert "audit/audit-index.sqlite3" not in source
    assert '"audit" / "audit-index.sqlite3"' not in source


async def test_no_index_yet_is_an_error_the_lane_can_report(tmp_path) -> None:
    missing = tmp_path / "audit" / "audit-index.sqlite3"
    reader = LazyAuditIndex(str(missing))

    with pytest.raises(FileNotFoundError) as raised:
        await reader.tail_for_owner("owner-1")

    # Named, so the events lane's detail says which file and why.
    assert "审计索引" in str(raised.value)
    assert str(missing) in str(raised.value)


async def test_an_index_that_appears_later_is_picked_up(tmp_path) -> None:
    """The race the startup check could only lose.

    The loop that creates the file starts in the same lifespan as the read
    handle. A handle that decided "absent" once would stay absent for the life
    of the process, which on a healthy Host is exactly wrong.
    """

    from eidolon_admin_server.audit import AuditIndexSettings, AuditIndexStore

    path = tmp_path / "audit" / "audit-index.sqlite3"
    reader = LazyAuditIndex(str(path))

    with pytest.raises(FileNotFoundError):
        await reader.tail_for_owner("owner-1")

    # Now the indexer gets there, as it does a moment after startup.
    writer = AuditIndexStore.open(AuditIndexSettings(sqlite_path=str(path)))
    await writer.init_schema()
    await writer.close()

    assert await reader.tail_for_owner("owner-1") == []
    await reader.close()


async def test_the_indexer_says_so_when_it_stops(caplog) -> None:
    """The other half of the silence.

    ``asyncio`` surfaces an unretrieved task exception at garbage collection,
    which in a long-lived process can be never — so the indexer dying looked
    exactly like the indexer working. It is worth telling: no index means the
    Owner's events lane is dark and every authority's dispatcher is publishing
    into a stream nobody created.
    """

    import asyncio
    import logging

    from eidolon_admin_server.app.main import _report_indexer_exit

    async def boom() -> None:
        raise RuntimeError("mkdir failed: read-only file system")

    task = asyncio.create_task(boom())
    with pytest.raises(RuntimeError):
        await task

    with caplog.at_level(logging.ERROR):
        _report_indexer_exit(task)

    assert "audit indexer stopped" in caplog.text
    assert "read-only file system" in caplog.text


async def test_a_cancelled_indexer_is_not_an_error() -> None:
    """Shutdown cancels it on purpose; that is not news."""

    import asyncio
    import logging

    from eidolon_admin_server.app.main import _report_indexer_exit

    async def forever() -> None:
        await asyncio.Event().wait()

    task = asyncio.create_task(forever())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # No logging assertion needed: the point is that it returns without asking
    # a cancelled task for an exception, which would raise.
    _report_indexer_exit(task)


async def test_a_dead_subscription_is_rebuilt_rather_than_asked_again() -> None:
    """A pull subscription can die while its socket stays perfectly healthy.

    Delete the stream or its durable consumer — recreating a stream does both —
    and every later fetch answers ServiceUnavailable forever. ``connect()``
    returns early while the connection is alive, so without dropping it the loop
    re-asks the same dead consumer every five seconds. Observed exactly that:
    three published events sat in a stream nobody was consuming.
    """

    from eidolon_admin_server.audit.jetstream import JetStreamAuditIndexer

    indexer = JetStreamAuditIndexer.__new__(JetStreamAuditIndexer)

    class _Connection:
        drained = False

        async def drain(self):
            type(self).drained = True
            raise RuntimeError("connection already gone")

    indexer._connection = _Connection()
    indexer._subscription = object()

    with pytest.raises(RuntimeError):
        await indexer.close()

    # Cleared despite the failure, so the next connect() rebuilds both.
    assert indexer._connection is None
    assert indexer._subscription is None
