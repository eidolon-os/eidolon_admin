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


# ---- watch is stubbed (Phase 26) -----------------------------------------


@pytest.mark.asyncio
async def test_watch_raises_not_implemented(kv: KVClient, bucket_name: str) -> None:
    # The router signature must be reservable today even though impl is pending.
    with pytest.raises(NotImplementedError) as excinfo:
        async for _ in kv.watch(bucket_name):  # noqa: B007
            break
    # Message must hint at Phase 26 so future devs find this stub immediately.
    assert "Phase 26" in str(excinfo.value)


# ---- json helpers (pure unit tests, no NATS needed) ----------------------


def test_json_round_trip_with_cjk() -> None:
    # ensure_ascii=False so Chinese soul.md stays readable on the wire.
    assert from_json_bytes(to_json_bytes({"name": "解之"})) == {"name": "解之"}


def test_from_json_bytes_handles_none() -> None:
    assert from_json_bytes(None) is None
