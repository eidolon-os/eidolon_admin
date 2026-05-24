"""NATS JetStream KV — primitive operations only.

This module is the *infrastructure* layer. It knows nothing about devices,
agents, souls, mappings, or any other business concept. It does one thing:
expose ``get / put / delete / list_keys / watch`` on top of named buckets,
plus an ``ensure_bucket`` that idempotently creates a bucket with the
caller-specified storage / size / history policy.

Why a separate client (not reusing ``JetStreamPublisher``):
    The publisher is hardcoded to publish to ``agent.memory.conversation.turn.*``
    subjects with the memory-turn payload shape. Bending it to also do
    generic KV ops would force it to grow two unrelated responsibilities.
    A dedicated client keeps each module honest about its surface area.

Why ``watch()`` is a stub:
    Plan Phase 25 explicitly scopes runtime-cache-sync to the next phase.
    The signature is fixed here so callers can wire dummies in tests and so
    the eventual implementation slots in with no API churn:

        async for op, key, value in kv.watch("souls"):
            ...

    ``op`` is one of "put" / "delete"; ``value`` is ``bytes`` for "put",
    ``None`` for "delete".
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from typing import Any, AsyncIterator, Literal

import nats
from nats.js.api import KeyValueConfig, StorageType
from nats.js.errors import KeyNotFoundError, NoKeysError

logger = logging.getLogger(__name__)


_DEFAULT_URL = "nats://127.0.0.1:4222"


def default_nats_url() -> str:
    """Read NATS URL from env with a sensible local-dev default."""
    return os.environ.get("EIDOLON_NATS_URL", _DEFAULT_URL)


@dataclass(frozen=True)
class BucketSpec:
    """How a bucket should be configured. ``ensure_bucket`` uses this."""

    name: str
    max_value_size: int  # bytes per single value; oversized PUT rejected by server
    history: int = 1     # how many revisions JetStream KV retains per key


WatchOp = Literal["put", "delete"]


class KVClient:
    """Thin async wrapper over ``nats.js.kv`` that:

    - lazy-connects on first call (so tests / startup don't block on a
      not-yet-running broker);
    - lets the caller declare bucket configs via ``ensure_bucket``;
    - never serializes / deserializes — that's the repository layer's job.
      ``put`` accepts ``bytes`` only; ``get`` returns ``bytes``. JSON
      structuring belongs above this layer.
    """

    def __init__(self, url: str | None = None) -> None:
        self._url = url or default_nats_url()
        self._nc: nats.NATS | None = None
        self._js: Any = None
        self._buckets: dict[str, Any] = {}
        self._connect_lock = asyncio.Lock()

    # ---- connection lifecycle ----------------------------------------------

    async def connect(self) -> None:
        """Establish the connection. Idempotent.

        Called once at admin startup. If NATS is unreachable, raises — the
        caller (main.py lifespan) decides whether to swallow that and mark
        ``app.state.nats_kv = None`` so /api/devices can return 503 cleanly.
        """
        if self._nc is not None and self._nc.is_connected:
            return
        async with self._connect_lock:
            if self._nc is not None and self._nc.is_connected:
                return
            self._nc = await nats.connect(
                self._url,
                allow_reconnect=True,
                max_reconnect_attempts=-1,
            )
            self._js = self._nc.jetstream()
            logger.info("KVClient connected to %s", self._url)

    async def close(self) -> None:
        if self._nc is not None:
            try:
                await self._nc.drain()
            except Exception:  # noqa: BLE001 — closing is best-effort
                logger.warning("NATS drain on close failed", exc_info=True)
            self._nc = None
            self._js = None
            self._buckets.clear()

    @property
    def is_connected(self) -> bool:
        return self._nc is not None and self._nc.is_connected

    # ---- bucket admin ------------------------------------------------------

    async def ensure_bucket(self, spec: BucketSpec) -> None:
        """Create the bucket if missing; otherwise leave it alone.

        Why we don't reconcile existing configs:
            If the operator has manually tuned a bucket (e.g. raised history
            to 50 to keep more soul revisions), silently overwriting that
            with our defaults on every admin start would erase intent. We
            adopt "if it exists, trust it" — drift visible via ``nats kv
            info``. Re-creating with new params requires the operator to
            delete first.
        """
        await self.connect()
        try:
            kv = await self._js.key_value(bucket=spec.name)
            logger.debug("bucket %s already exists, reusing", spec.name)
        except Exception:  # noqa: BLE001 — nats-py raises generic on missing
            kv = await self._js.create_key_value(
                KeyValueConfig(
                    bucket=spec.name,
                    max_value_size=spec.max_value_size,
                    history=spec.history,
                    storage=StorageType.FILE,
                )
            )
            logger.info(
                "created KV bucket %s (max_value=%d, history=%d, storage=file)",
                spec.name,
                spec.max_value_size,
                spec.history,
            )
        self._buckets[spec.name] = kv

    # ---- value ops ---------------------------------------------------------

    async def get(self, bucket: str, key: str) -> bytes | None:
        """Return raw bytes, or ``None`` if the key is absent.

        Translating "missing key" to None (instead of bubbling
        ``KeyNotFoundError``) keeps the repository layer free of
        infrastructure-specific exception handling — "missing" becomes a
        normal value the business code reasons about.
        """
        kv = await self._kv(bucket)
        try:
            entry = await kv.get(key)
        except KeyNotFoundError:
            return None
        return entry.value

    async def put(self, bucket: str, key: str, value: bytes) -> None:
        kv = await self._kv(bucket)
        await kv.put(key, value)

    async def delete(self, bucket: str, key: str) -> None:
        """Delete is idempotent — missing key is a no-op, not an error.

        This matters because admin orchestrator's rollback path may try to
        delete a key that was never written (e.g. error happened before the
        put). Forcing the caller to check-then-delete is just noise.
        """
        kv = await self._kv(bucket)
        try:
            await kv.delete(key)
        except KeyNotFoundError:
            pass

    async def list_keys(self, bucket: str, prefix: str = "") -> list[str]:
        """All keys in the bucket, optionally filtered by string prefix.

        NATS KV doesn't have native prefix indexing — this is a full key
        scan filtered client-side. Fine at the scale this project targets
        (hundreds of devices); revisit if/when bucket cardinality grows.
        """
        kv = await self._kv(bucket)
        try:
            all_keys = await kv.keys()
        except NoKeysError:
            return []
        if not prefix:
            return list(all_keys)
        return [k for k in all_keys if k.startswith(prefix)]

    # ---- watch (STUB — Phase 26 implements) --------------------------------

    async def watch(self, bucket: str) -> AsyncIterator[tuple[WatchOp, str, bytes | None]]:
        """STUB. Will yield ``(op, key, value)`` tuples on KV change.

        ``op`` is "put" or "delete"; ``value`` is the new bytes on "put",
        ``None`` on "delete". The caller treats this as an async iterator
        and never sees the underlying nats-py watch primitive.

        Why a stub now: Phase 25 admin is single-instance and reads NATS
        through directly on every request (no in-process cache that needs
        invalidation). Multi-instance / cache-warming is Phase 26's problem.
        Reserving the signature today means Phase 26 lands without API
        churn at the call sites.
        """
        raise NotImplementedError(
            f"KVClient.watch({bucket!r}) is stubbed — Phase 26 will implement. "
            "Until then, callers must read-through on each request."
        )
        # The yield below is unreachable but tells type checkers this is
        # an async generator (so the return type holds).
        yield ("put", "", None)  # pragma: no cover

    # ---- internals ---------------------------------------------------------

    async def _kv(self, bucket: str) -> Any:
        if bucket not in self._buckets:
            raise RuntimeError(
                f"bucket {bucket!r} not registered — call ensure_bucket() first "
                "(usually at app startup in lifespan)"
            )
        return self._buckets[bucket]


# ---- json helpers (kept here so callers don't duplicate the pattern) -------
#
# These are tiny conveniences for the common case: callers that *do* want
# JSON-shaped values still go through this module so all bytes ↔ python
# conversion lives in one place. Repository layer uses these; raw bytes
# users (souls bucket) bypass.


def to_json_bytes(value: Any) -> bytes:
    """``json.dumps`` with utf-8 encoding + no ASCII-escaping for CJK content."""
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def from_json_bytes(raw: bytes | None) -> Any | None:
    if raw is None:
        return None
    return json.loads(raw.decode("utf-8"))
