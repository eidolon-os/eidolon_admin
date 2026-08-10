"""NATS JetStream KV — primitive operations only.

This module is the *infrastructure* layer. It knows nothing about devices,
agents, souls, mappings, or any other business concept. It does one thing:
expose ``get / put / delete / list_keys / watch`` on top of named buckets,
plus an ``ensure_bucket`` that idempotently creates a bucket with the
caller-specified storage / size / history policy.

Why a separate client (not reusing ``JetStreamPublisher``):
    The publisher is hardcoded to publish to ``eidolon.memory.turn.*``
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
from nats.errors import ConnectionClosedError, ConnectionReconnectingError
from nats.js import kv as nats_kv  # for KV_DEL / KV_PURGE op constants in watch()
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

    async def connect(
        self,
        *,
        max_attempts: int = 1,
        initial_delay: float = 0.5,
    ) -> None:
        """Establish the connection. Idempotent.

        Called once at admin startup. The supervisord-level ``wait-tcp``
        gate (Phase 30.A) makes NATS reachable BEFORE admin's lifespan
        runs in the normal ``run_all.sh start/restart`` path. This retry
        is defense-in-depth for the niche case ``sv restart admin:admin-api``
        catches NATS itself in the middle of a restart:

          - ``max_attempts=1`` (default): single shot, fast-fail. Used by
            tests and by callers that prefer to react to the error
            themselves.
          - ``max_attempts=5`` + ``initial_delay=0.5``: ~15s worst case
            (0.5s → 1s → 2s → 4s → 8s capped). Lifespan passes this so a
            transient NATS hiccup doesn't degrade admin to "all registry
            routes 503" for the rest of the process lifetime.

        If all attempts fail, raises ``ConnectionError`` once — the
        caller (main.py lifespan) swallows it and leaves orchestrators
        as None, same as before.
        """
        if self._nc is not None and self._nc.is_connected:
            return
        async with self._connect_lock:
            if self._nc is not None and self._nc.is_connected:
                return

            last_exc: Exception | None = None
            delay = initial_delay
            for attempt in range(1, max_attempts + 1):
                try:
                    self._nc = await asyncio.wait_for(
                        nats.connect(
                            self._url,
                            connect_timeout=2,
                            allow_reconnect=True,
                            max_reconnect_attempts=0,
                        ),
                        timeout=3.0,
                    )
                    break  # success — exit the retry loop
                except Exception as exc:  # noqa: BLE001 — broad on purpose
                    last_exc = exc
                    if attempt < max_attempts:
                        logger.info(
                            "KVClient connect attempt %d/%d to %s failed "
                            "(%s); retrying in %.1fs",
                            attempt,
                            max_attempts,
                            self._url,
                            type(exc).__name__,
                            delay,
                        )
                        await asyncio.sleep(delay)
                        # Exponential backoff, capped at 8s so a long
                        # outage doesn't push us to multi-minute waits.
                        delay = min(delay * 2, 8.0)
            else:
                # No break = every attempt failed. Raise once, summarized.
                raise ConnectionError(
                    f"could not connect to NATS at {self._url!r} after "
                    f"{max_attempts} attempt(s)"
                ) from last_exc

            self._js = self._nc.jetstream()
            logger.info("KVClient connected to %s", self._url)

    async def close(self) -> None:
        """Release the NATS connection. Best-effort, never raises.

        Why this is more elaborate than a single ``drain()``:
            ``drain()`` requires a fully-connected socket — it flushes
            outstanding publishes then closes cleanly. But our lifespan
            tears admin down in an arbitrary order relative to nats-server
            (supervisord stops both ~simultaneously). If nats-server beat
            us to it, the client is in the "reconnecting" state and
            ``drain()`` immediately raises ``ConnectionReconnectingError``,
            which the previous version dutifully logged with a full
            traceback at WARNING level — pure stderr noise during normal
            shutdown.

            The right behavior is:
              1. Try ``drain()`` only when actually connected (graceful
                 flush of any pending writes — there usually aren't any).
              2. If we're in any transient state (reconnecting / closed)
                 skip drain and go straight to ``close()``.
              3. ``close()`` itself is forgiving — already-closed
                 connections just no-op.

            Anything still throwing past step 3 is genuinely unexpected
            and worth logging.
        """
        nc = self._nc
        if nc is None:
            return
        # Always null these out first so a partial-close failure doesn't
        # leave the next call to ``connect()`` thinking we're still attached.
        self._nc = None
        self._js = None
        self._buckets.clear()

        if nc.is_connected:
            try:
                await nc.drain()
                return  # drain() implies close() — no need for both
            except ConnectionReconnectingError:
                # Lost the connection between is_connected check and drain;
                # fall through to close() for socket cleanup.
                pass
            except ConnectionClosedError:
                return  # someone else already finished the job
            except Exception:  # noqa: BLE001
                logger.warning("NATS drain on close failed", exc_info=True)
                # still fall through to close() to be sure the socket is gone

        try:
            await nc.close()
        except ConnectionClosedError:
            pass  # already closed — fine
        except Exception:  # noqa: BLE001
            logger.warning("NATS close failed", exc_info=True)

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

    async def open_bucket(self, bucket: str) -> None:
        """Attach to an existing bucket without creating or modifying it.

        Read-only observability surfaces use this instead of ``ensure_bucket``:
        a missing authoritative bucket must be reported as unavailable, not
        silently created by an Admin GET request.
        """
        if bucket in self._buckets:
            return
        await self.connect()
        self._buckets[bucket] = await self._js.key_value(bucket=bucket)

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

    async def get_existing(self, bucket: str, key: str) -> bytes | None:
        """Read a key from an existing bucket without creating that bucket.

        This is the read-only counterpart to :meth:`ensure_bucket`.  Observer
        surfaces (for example Mission Control) must be able to consume a
        bucket owned by another service without becoming a second bucket
        creator or writer.  The bucket handle is cached, but values are always
        fetched directly from JetStream on every call.
        """
        await self.connect()
        if bucket not in self._buckets:
            self._buckets[bucket] = await self._js.key_value(bucket=bucket)
        return await self.get(bucket, key)

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

    # ---- watch -------------------------------------------------------------

    async def watch(
        self,
        bucket: str,
        *,
        include_history: bool = False,
    ) -> AsyncIterator[tuple[WatchOp, str, bytes | None]]:
        """Stream KV change events as ``(op, key, value)`` tuples.

        ``op`` is ``"put"`` or ``"delete"``. ``value`` is the new bytes
        on a put, ``None`` on a delete. Returned by the underlying
        async iterator until the caller breaks out of the loop, at
        which point we stop the watcher and unsubscribe.

        Delivery semantics (matching nats-py's ``KeyValue.watch``):

        - ``include_history=False`` (default, snapshot mode): the watcher
          first delivers the *current value* of every key in the bucket
          (one event per key, at its latest revision), then continues
          with future put/delete events. This is the right mode for
          cache-warming: subscriber sees the world as it stands, then
          stays in sync. The watcher does NOT replay older revisions
          that have been overwritten.

        - ``include_history=True`` (full replay): every revision of every
          key is delivered in order, then live updates. Use for audit
          / rebuild flows that need the full history.

        Either mode silently drops nats-py's "init complete" sentinel
        (the lone ``None`` entry) — callers see only real events.

        Why this matters now (Phase 26):
            Multi-admin coordination + future in-process caches both
            need this. Phase 25 left it stubbed because admin was
            single-instance and read-through was correct + simple.
            With this implementation the same signature now does what
            its docstring promises — no caller has to change.
        """
        kv = await self._kv(bucket)
        watcher = await kv.watch(
            keys=">",  # nats-py KV wildcard for "every key in bucket"
            include_history=include_history,
        )
        try:
            async for entry in watcher:
                # ``None`` is the init-complete sentinel from nats-py:
                # "history replay finished, you're live now". Callers
                # don't care about this — they want events, not lifecycle
                # markers. Drop and continue.
                if entry is None:
                    continue
                op: WatchOp
                if entry.operation in (nats_kv.KV_DEL, nats_kv.KV_PURGE):
                    op = "delete"
                    value: bytes | None = None
                else:
                    op = "put"
                    value = entry.value
                yield (op, entry.key, value)
        finally:
            # Best-effort stop — if the connection's already gone the
            # unsubscribe fails harmlessly and we don't want that
            # masking the original exit reason.
            try:
                await watcher.stop()
            except Exception:  # noqa: BLE001
                logger.debug("KVClient.watch: stop() failed; ignored", exc_info=True)

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
