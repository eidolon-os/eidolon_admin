"""JetStream pull consumer for the rebuildable Admin audit index."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from eidolon_sdk.biz.audit import AuditEnvelope
from eidolon_sdk.integrations.audit import (
    AUDIT_STREAM_NAME,
    AUDIT_SUBJECT_PREFIX,
    ensure_audit_stream,
)

from .index import AuditIndexStore


@dataclass(frozen=True)
class AuditJetStreamSettings:
    url: str = "nats://127.0.0.1:4222"
    #: Named by the SDK, which is also where the publishers read it from. The
    #: stream's shape is a contract between repositories, and this side used to
    #: hold a private copy of it — along with the only code that created it.
    stream: str = AUDIT_STREAM_NAME
    subject_prefix: str = AUDIT_SUBJECT_PREFIX
    durable_consumer: str = "eidolon-audit-indexer-v1"
    fetch_batch: int = 200
    fetch_timeout_seconds: float = 1.0
    connect_timeout_seconds: float = 2.0
    reconnect_attempts: int = 3
    reconnect_wait_seconds: float = 0.25


class JetStreamAuditIndexer:
    """Commit one idempotent projection batch before ACKing messages."""

    def __init__(self, index: AuditIndexStore, settings: AuditJetStreamSettings) -> None:
        self._index = index
        self.settings = settings
        self._connection: Any | None = None
        self._subscription: Any | None = None

    async def connect(self) -> None:
        if self._connection is not None:
            return
        import nats

        connection = await nats.connect(
            self.settings.url,
            name="eidolon-audit-indexer",
            allow_reconnect=True,
            connect_timeout=self.settings.connect_timeout_seconds,
            max_reconnect_attempts=self.settings.reconnect_attempts,
            reconnect_time_wait=self.settings.reconnect_wait_seconds,
        )
        jetstream = connection.jetstream()
        try:
            # Ensured, not asserted. Publishers ensure it too, so neither side
            # waits for the other — but on a Host where nothing has published
            # yet, refusing to subscribe until something does would turn a quiet
            # Host into a failing one.
            await ensure_audit_stream(jetstream)
            subscription = await jetstream.pull_subscribe(
                f"{self.settings.subject_prefix}.>",
                durable=self.settings.durable_consumer,
                stream=self.settings.stream,
            )
        except Exception:
            await connection.close()
            raise
        self._connection = connection
        self._subscription = subscription

    async def close(self) -> None:
        connection = self._connection
        # Cleared first, and unconditionally. A drain that raises used to leave
        # the handles in place, so the retry path's ``close()`` did not actually
        # reopen anything: ``connect()`` saw a connection and returned, and the
        # loop went on asking a subscription that was already dead.
        self._connection = None
        self._subscription = None
        if connection is not None:
            await connection.drain()

    async def consume_once(self) -> int:
        await self.connect()
        subscription = self._subscription
        assert subscription is not None

        try:
            messages = await subscription.fetch(
                self.settings.fetch_batch,
                timeout=self.settings.fetch_timeout_seconds,
            )
        # The builtin, deliberately. ``nats.errors.TimeoutError`` is a *subclass*
        # of it, so catching the builtin covers both — while catching only the
        # nats one missed the bare ``asyncio.TimeoutError`` that nats-py raises
        # when a fetch's deadline runs out. Nothing waiting is the most ordinary
        # thing an idle indexer does, and it was arriving at the caller as a
        # failure: a full traceback and a five-second backoff, 235 times on one
        # Host, in the log a real failure would have to be found in.
        except TimeoutError:
            return 0
        envelopes: list[AuditEnvelope] = []
        valid_messages: list[Any] = []
        for message in messages:
            try:
                envelope = AuditEnvelope.model_validate_json(message.data)
            except Exception:
                await message.term()
                continue
            envelopes.append(envelope)
            valid_messages.append(message)
        if not envelopes:
            return 0
        await self._index.ingest(envelopes)
        await asyncio.gather(*(message.ack() for message in valid_messages))
        return len(envelopes)
