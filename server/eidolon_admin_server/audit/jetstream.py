"""JetStream pull consumer for the rebuildable Admin audit index."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from eidolon_sdk.biz.audit import AuditEnvelope

from .index import AuditIndexStore


@dataclass(frozen=True)
class AuditJetStreamSettings:
    url: str = "nats://127.0.0.1:4222"
    stream: str = "EIDOLON_AUDIT_V1"
    subject_prefix: str = "eidolon.audit.v1"
    durable_consumer: str = "eidolon-audit-indexer-v1"
    max_age: timedelta = timedelta(days=30)
    max_bytes: int = 512 * 1024 * 1024
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
            await _ensure_stream(jetstream, self.settings)
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
        if self._connection is not None:
            await self._connection.drain()
        self._connection = None
        self._subscription = None

    async def consume_once(self) -> int:
        await self.connect()
        subscription = self._subscription
        assert subscription is not None
        from nats.errors import TimeoutError as NatsTimeoutError

        try:
            messages = await subscription.fetch(
                self.settings.fetch_batch,
                timeout=self.settings.fetch_timeout_seconds,
            )
        except NatsTimeoutError:
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


async def _ensure_stream(jetstream: Any, settings: AuditJetStreamSettings) -> None:
    from nats.js import api
    from nats.js.errors import NotFoundError

    try:
        await jetstream.stream_info(settings.stream)
        return
    except NotFoundError:
        pass
    await jetstream.add_stream(
        config=api.StreamConfig(
            name=settings.stream,
            subjects=[f"{settings.subject_prefix}.>"],
            retention=api.RetentionPolicy.LIMITS,
            storage=api.StorageType.FILE,
            max_age=settings.max_age.total_seconds(),
            max_bytes=settings.max_bytes,
        )
    )
