"""How fast a claim window's Setup code can be guessed at, per transport.

ADR-0007 took the lockout away on purpose: a window that closes on the fifth
wrong code is a device bricked out of its box by someone across the room, and
the factory window cannot be reopened by anyone. What it left in its place was
nothing at all — a BLE link answers about ten guesses a second and the LAN as
many as the Host can serve, against 10^8 codes and a window that never expires.

So guesses are paced instead of counted to a cliff. Every attempt — a PAKE
start, a legacy ``session.authenticate``, a legacy LAN claim — spends a token
before the Host looks at the code; a token comes back with time. Nothing here
ever closes a window, so the denial of service ADR-0007 removed does not come
back: the worst an attacker can do is make the legitimate phone wait, and only
on the transport the attacker is using. BLE and the LAN draw from separate
buckets, because the person standing next to the Host must still be able to
claim it while something on the network is hammering it.

State is in memory and covers the open window only. A restart refills every
bucket, which costs an attacker a restart of the Host per burst; a new window
starts fresh, which is the point of minting one.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from typing import Callable


class GuessRefused(Exception):
    """No token for this attempt; ``retry_after_ms`` says when there will be one."""

    def __init__(self, retry_after_ms: int) -> None:
        super().__init__(f"Too many Setup code attempts; retry in {retry_after_ms} ms")
        self.retry_after_ms = retry_after_ms


@dataclass(frozen=True)
class Pace:
    """A bucket of ``capacity`` tokens, one returned every ``period`` seconds."""

    capacity: int
    period: float


@dataclass
class _Bucket:
    pace: Pace
    tokens: float
    updated: float

    def refill(self, now: float) -> None:
        elapsed = max(0.0, now - self.updated)
        self.tokens = min(self.pace.capacity, self.tokens + elapsed / self.pace.period)
        self.updated = now

    def wait_ms(self) -> int:
        if self.tokens >= 1:
            return 0
        return max(1, math.ceil((1 - self.tokens) * self.pace.period * 1000))


BLE_PACE = Pace(capacity=10, period=2.0)
LAN_SOURCE_PACE = Pace(capacity=5, period=20.0)
LAN_PACE = Pace(capacity=10, period=10.0)
#: Past this many wrong codes on one window over the LAN, the LAN as a whole
#: slows to this pace. A person typing is nowhere near it.
LAN_SLOW_AFTER_FAILURES = 100
LAN_SLOW_PACE = Pace(capacity=10, period=60.0)
#: How many LAN sources are remembered at once. An attacker with more
#: addresses than this still meets the shared LAN bucket.
MAX_LAN_SOURCES = 256


class GuessBudget:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._window: str | None = None
        self._ble: _Bucket | None = None
        self._lan: _Bucket | None = None
        self._sources: dict[str, _Bucket] = {}
        self._lan_failures = 0

    def take(self, *, window: str, transport: str, source: str | None = None) -> None:
        """Spend one guess on ``window`` or raise GuessRefused."""

        with self._lock:
            now = self._clock()
            self._enter(window, now)
            if transport == "ble":
                bucket = self._ble
                assert bucket is not None
                bucket.refill(now)
                if bucket.tokens < 1:
                    raise GuessRefused(bucket.wait_ms())
                bucket.tokens -= 1
                return
            if transport != "lan":
                raise ValueError(f"unknown transport {transport!r}")
            shared = self._lan
            assert shared is not None
            if self._lan_failures >= LAN_SLOW_AFTER_FAILURES and shared.pace != LAN_SLOW_PACE:
                shared.refill(now)
                shared.pace = LAN_SLOW_PACE
                shared.tokens = min(shared.tokens, LAN_SLOW_PACE.capacity)
            mine = self._source(source or "unknown", now)
            shared.refill(now)
            mine.refill(now)
            # Both or neither: a refused attempt spends nothing, or a busy
            # neighbour would drain a source that never got to guess.
            wait = max(shared.wait_ms(), mine.wait_ms())
            if wait:
                raise GuessRefused(wait)
            shared.tokens -= 1
            mine.tokens -= 1

    def failed(self, *, window: str, transport: str) -> None:
        """A guess on ``window`` was wrong. Only the LAN acts on the count."""

        with self._lock:
            if window != self._window or transport != "lan":
                return
            self._lan_failures += 1

    def _enter(self, window: str, now: float) -> None:
        if window == self._window:
            return
        self._window = window
        self._ble = _Bucket(BLE_PACE, BLE_PACE.capacity, now)
        self._lan = _Bucket(LAN_PACE, LAN_PACE.capacity, now)
        self._sources = {}
        self._lan_failures = 0

    def _source(self, source: str, now: float) -> _Bucket:
        bucket = self._sources.get(source)
        if bucket is not None:
            return bucket
        if len(self._sources) >= MAX_LAN_SOURCES:
            # Forget a source that has fully recovered first; if none has,
            # the oldest. Either way the shared bucket still binds them.
            for key, candidate in list(self._sources.items()):
                candidate.refill(now)
                if candidate.tokens >= candidate.pace.capacity:
                    del self._sources[key]
                    break
            else:
                del self._sources[next(iter(self._sources))]
        bucket = _Bucket(LAN_SOURCE_PACE, LAN_SOURCE_PACE.capacity, now)
        self._sources[source] = bucket
        return bucket
