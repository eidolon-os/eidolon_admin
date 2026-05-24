"""Tests for the KVClient primitives.

These hit a real local NATS server (the one supervisord brings up at
127.0.0.1:4222). We skip the whole file when NATS is unreachable so the
suite doesn't fail in environments without it.

Each test creates a bucket with a UUID-suffixed name so parallel runs and
re-runs don't collide on state — we never clean up buckets (NATS will GC
them by retention policy; for tests they're effectively ephemeral).
"""
from __future__ import annotations

import uuid

import pytest

from eidolon_admin_server.app.nats_kv import (
    BucketSpec,
    KVClient,
    from_json_bytes,
    to_json_bytes,
)


# ---- skip-if-NATS-down --------------------------------------------------


async def _can_reach_nats() -> bool:
    client = KVClient()
    try:
        await client.connect()
        return True
    except Exception:
        return False
    finally:
        try:
            await client.close()
        except Exception:
            pass


@pytest.fixture
async def kv() -> KVClient:
    if not await _can_reach_nats():
        pytest.skip("NATS not reachable at 127.0.0.1:4222")
    client = KVClient()
    await client.connect()
    yield client
    await client.close()


@pytest.fixture
def bucket_name() -> str:
    """Unique per-test bucket so concurrent runs don't fight."""
    return f"test_kv_{uuid.uuid4().hex[:12]}"


# ---- ensure_bucket -------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_bucket_creates_new(kv: KVClient, bucket_name: str) -> None:
    await kv.ensure_bucket(BucketSpec(name=bucket_name, max_value_size=1024))
    # Idempotency: calling twice doesn't blow up.
    await kv.ensure_bucket(BucketSpec(name=bucket_name, max_value_size=1024))


# ---- put / get / delete --------------------------------------------------


@pytest.mark.asyncio
async def test_put_get_round_trip(kv: KVClient, bucket_name: str) -> None:
    await kv.ensure_bucket(BucketSpec(name=bucket_name, max_value_size=1024))
    await kv.put(bucket_name, "k1", b"hello")
    assert await kv.get(bucket_name, "k1") == b"hello"


@pytest.mark.asyncio
async def test_get_missing_returns_none(kv: KVClient, bucket_name: str) -> None:
    await kv.ensure_bucket(BucketSpec(name=bucket_name, max_value_size=1024))
    assert await kv.get(bucket_name, "never_set") is None


@pytest.mark.asyncio
async def test_delete_then_get_returns_none(kv: KVClient, bucket_name: str) -> None:
    await kv.ensure_bucket(BucketSpec(name=bucket_name, max_value_size=1024))
    await kv.put(bucket_name, "k1", b"v")
    await kv.delete(bucket_name, "k1")
    assert await kv.get(bucket_name, "k1") is None


@pytest.mark.asyncio
async def test_delete_missing_is_idempotent(kv: KVClient, bucket_name: str) -> None:
    await kv.ensure_bucket(BucketSpec(name=bucket_name, max_value_size=1024))
    # Should not raise — compensation paths rely on this contract.
    await kv.delete(bucket_name, "never_existed")


# ---- list_keys -----------------------------------------------------------


@pytest.mark.asyncio
async def test_list_keys_returns_all(kv: KVClient, bucket_name: str) -> None:
    await kv.ensure_bucket(BucketSpec(name=bucket_name, max_value_size=1024))
    await kv.put(bucket_name, "a.1", b"a")
    await kv.put(bucket_name, "a.2", b"b")
    await kv.put(bucket_name, "b.1", b"c")
    assert set(await kv.list_keys(bucket_name)) == {"a.1", "a.2", "b.1"}


@pytest.mark.asyncio
async def test_list_keys_prefix_filters(kv: KVClient, bucket_name: str) -> None:
    await kv.ensure_bucket(BucketSpec(name=bucket_name, max_value_size=1024))
    await kv.put(bucket_name, "device.x", b"1")
    await kv.put(bucket_name, "device.y", b"2")
    await kv.put(bucket_name, "agent.z", b"3")
    assert set(await kv.list_keys(bucket_name, prefix="device.")) == {"device.x", "device.y"}


@pytest.mark.asyncio
async def test_list_keys_empty_bucket(kv: KVClient, bucket_name: str) -> None:
    await kv.ensure_bucket(BucketSpec(name=bucket_name, max_value_size=1024))
    assert await kv.list_keys(bucket_name) == []


# ---- watch ---------------------------------------------------------------
#
# These exercise the real nats-py KV watcher. We drive put/delete operations
# from one asyncio task and observe events from another that's consuming the
# watch iterator. No mocks — the broker is the only source of events.


import asyncio  # noqa: E402 — needed by watch tests below


async def _drain_until(
    watcher: AsyncIterator[tuple[str, str, bytes | None]],  # type: ignore[type-arg]
    predicate,
    *,
    timeout: float = 3.0,
) -> tuple[str, str, bytes | None]:
    """Read events from ``watcher`` until ``predicate(event)`` returns True.

    Used because the underlying NATS subject is bucket-scoped, but bucket
    names are per-test unique — so any event we see IS from our test.
    The predicate lets us assert "the put I made arrives" without
    enumerating ordering between concurrent test flows.
    """
    async def _consume() -> tuple[str, str, bytes | None]:
        async for evt in watcher:
            if predicate(evt):
                return evt
        raise RuntimeError("watcher exited without matching event")

    return await asyncio.wait_for(_consume(), timeout=timeout)


@pytest.mark.asyncio
async def test_watch_emits_put_event_for_real_write(
    kv: KVClient, bucket_name: str,
) -> None:
    """Putting a key after the watcher starts produces a ('put', key, value)
    event. The simplest correctness property — without this, the whole
    feature is broken.
    """
    await kv.ensure_bucket(BucketSpec(name=bucket_name, max_value_size=1024))

    watcher = kv.watch(bucket_name).__aiter__()

    async def _writer():
        # Tiny pause so the watcher's subscribe is established before we
        # publish; nats-py's watch is async-eventually-consistent.
        await asyncio.sleep(0.05)
        await kv.put(bucket_name, "alpha", b"hello")

    write_task = asyncio.create_task(_writer())
    try:
        evt = await _drain_until(watcher, lambda e: e[1] == "alpha")
        assert evt == ("put", "alpha", b"hello")
    finally:
        await write_task


@pytest.mark.asyncio
async def test_watch_emits_delete_event_with_none_value(
    kv: KVClient, bucket_name: str,
) -> None:
    """Deleting a key emits ('delete', key, None) — the documented shape.

    Value is None on delete (rather than echoing the previous bytes)
    because NATS KV delete tombstones don't carry payload. Callers
    depending on the value-on-delete contract would silently get wrong
    data; this test pins None explicitly.
    """
    await kv.ensure_bucket(BucketSpec(name=bucket_name, max_value_size=1024))
    await kv.put(bucket_name, "beta", b"initial")

    watcher = kv.watch(bucket_name).__aiter__()

    async def _writer():
        await asyncio.sleep(0.05)
        await kv.delete(bucket_name, "beta")

    write_task = asyncio.create_task(_writer())
    try:
        evt = await _drain_until(
            watcher,
            lambda e: e[1] == "beta" and e[0] == "delete",
        )
        assert evt == ("delete", "beta", None)
    finally:
        await write_task


@pytest.mark.asyncio
async def test_watch_snapshot_mode_emits_current_state_then_live_updates(
    kv: KVClient, bucket_name: str,
) -> None:
    """``include_history=False`` (default) is snapshot-then-live mode.

    Each key existing at watch-start is delivered ONCE at its current
    value (one event per key, not per revision), then the watcher
    continues streaming live changes. This matches nats-py's
    ``DeliverPolicy.LAST_PER_SUBJECT`` semantics.

    Why this is the useful default: it lets a cache subscriber populate
    its in-memory map by consuming the initial wave of events, then
    stay in sync via subsequent events — with no race window between
    "read all current state" and "subscribe to updates".
    """
    await kv.ensure_bucket(BucketSpec(name=bucket_name, max_value_size=1024))
    # Pre-existing keys with multiple revisions each — only the LATEST
    # revision per key should appear in snapshot mode.
    await kv.put(bucket_name, "old-1", b"v1-r1")
    await kv.put(bucket_name, "old-1", b"v1-r2")  # overwrites
    await kv.put(bucket_name, "old-2", b"v2-r1")

    watcher = kv.watch(bucket_name).__aiter__()

    # Snapshot pass: expect exactly the two current values, no
    # superseded revisions. Order isn't part of the contract.
    snapshot: dict[str, bytes | None] = {}
    for _ in range(2):
        op, key, value = await asyncio.wait_for(watcher.__anext__(), timeout=3.0)
        assert op == "put"
        snapshot[key] = value
    assert snapshot == {"old-1": b"v1-r2", "old-2": b"v2-r1"}

    # Live pass: a fresh write after snapshot must be observed too.
    async def _writer():
        await asyncio.sleep(0.05)
        await kv.put(bucket_name, "fresh", b"new")

    write_task = asyncio.create_task(_writer())
    try:
        evt = await asyncio.wait_for(watcher.__anext__(), timeout=3.0)
        assert evt == ("put", "fresh", b"new")
    finally:
        await write_task


@pytest.mark.asyncio
async def test_watch_with_history_replays_pre_existing_keys_first(
    kv: KVClient, bucket_name: str,
) -> None:
    """``include_history=True`` replays existing entries first, then the
    live stream continues. Mirror of the no-history test.

    Cache-warming use case: a fresh admin instance can subscribe with
    history=True, populate its in-memory cache from the replay, then
    seamlessly continue handling live updates without a race window
    between "read all" and "subscribe to updates".
    """
    await kv.ensure_bucket(BucketSpec(name=bucket_name, max_value_size=1024))
    await kv.put(bucket_name, "old-1", b"v1")
    await kv.put(bucket_name, "old-2", b"v2")

    watcher = kv.watch(bucket_name, include_history=True).__aiter__()

    collected: list[tuple[str, str, bytes | None]] = []
    # Collect both pre-existing entries (any order — nats-py replay
    # order is by revision but tests shouldn't depend on it).
    for _ in range(2):
        evt = await asyncio.wait_for(watcher.__anext__(), timeout=3.0)
        collected.append(evt)
    keys_seen = {e[1] for e in collected}
    assert keys_seen == {"old-1", "old-2"}, (
        f"expected history replay to surface both pre-existing keys; "
        f"got {keys_seen}"
    )


@pytest.mark.asyncio
async def test_watch_exits_cleanly_when_caller_breaks(
    kv: KVClient, bucket_name: str,
) -> None:
    """Breaking out of the ``async for`` loop must unsubscribe + release
    resources. Tested indirectly: opening a new watch on the same
    bucket immediately after must work (it wouldn't if we leaked the
    previous subscription's state across the connection).
    """
    await kv.ensure_bucket(BucketSpec(name=bucket_name, max_value_size=1024))
    # Seed one key so the first watch has something to deliver via the
    # snapshot pass; otherwise ``async for`` would block waiting for
    # an event that never comes, and the ``break`` is unreachable.
    await kv.put(bucket_name, "seed", b"seed")

    # First watch — consume the seed event, break immediately.
    async for _ in kv.watch(bucket_name):
        break

    # Second watch — must function normally on the same bucket.
    watcher = kv.watch(bucket_name).__aiter__()

    async def _writer():
        # Give the new watcher's subscribe a moment, then put a fresh key.
        await asyncio.sleep(0.05)
        await kv.put(bucket_name, "after-restart", b"ok")

    write_task = asyncio.create_task(_writer())
    try:
        # The first event may be the snapshot of "seed" — drain past it
        # and find our fresh write. (Don't rely on snapshot ordering.)
        seen: list[tuple[str, str, bytes | None]] = []
        for _ in range(3):  # at most: seed + after-restart + maybe one stray
            evt = await asyncio.wait_for(watcher.__anext__(), timeout=3.0)
            seen.append(evt)
            if evt[1] == "after-restart":
                break
        assert ("put", "after-restart", b"ok") in seen
    finally:
        await write_task


# ---- json helpers (pure unit tests, no NATS needed) ----------------------


def test_json_round_trip_with_cjk() -> None:
    # ensure_ascii=False so Chinese soul.md stays readable on the wire.
    assert from_json_bytes(to_json_bytes({"name": "解之"})) == {"name": "解之"}


def test_from_json_bytes_handles_none() -> None:
    assert from_json_bytes(None) is None
