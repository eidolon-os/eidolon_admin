"""NATS JetStream publisher for conversation turns.

memory's worker subscribes to ``agent.memory.conversation.turn.<user_id>`` to
ingest turns. We publish there from POST /api/memory/memories. Fire-and-forget;
the worker's ack is its own business.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from eidolon_sdk.memory import ConversationTurnPayload, conversation_turn_subject

import nats
from nats.errors import TimeoutError as NatsTimeoutError
from nats.js.errors import APIError

logger = logging.getLogger(__name__)


_DEFAULT_NATS_URL = "nats://127.0.0.1:4222"


def nats_url() -> str:
    return os.environ.get("EIDOLON_MEMORY_NATS_URL", _DEFAULT_NATS_URL)


def turn_subject(user_id: str) -> str:
    return conversation_turn_subject(user_id)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class JetStreamPublisher:
    """Lazy-connecting publisher. Reconnects on failure.

    One instance is shared across the FastAPI app, attached to app.state at
    startup and closed at shutdown.
    """

    def __init__(self, url: str | None = None) -> None:
        self._url = url or nats_url()
        self._nc: nats.NATS | None = None
        self._js: Any = None
        self._lock = asyncio.Lock()

    async def _ensure_connected(self) -> None:
        if self._nc is not None and self._nc.is_connected:
            return
        async with self._lock:
            if self._nc is not None and self._nc.is_connected:
                return
            self._nc = await nats.connect(self._url, allow_reconnect=True, max_reconnect_attempts=-1)
            self._js = self._nc.jetstream()
            logger.info("connected to NATS at %s", self._url)

    async def publish_turn(
        self,
        *,
        user_id: str,
        user_text: str,
        assistant_text: str,
        session_id: str = "",
        metadata: dict[str, Any] | None = None,
        timeout_seconds: float = 5.0,
    ) -> str:
        """Publish a ConversationTurnPayload. Returns the generated turn_id."""
        await self._ensure_connected()
        assert self._js is not None
        turn_id = uuid.uuid4().hex
        payload = ConversationTurnPayload(
            turn_id=turn_id,
            user_text=user_text,
            assistant_text=assistant_text,
            timestamp=_now_iso(),
            session_id=session_id,
            user_id=user_id,
            metadata=metadata or {},
        )
        body = json.dumps(payload.model_dump(mode="json"), ensure_ascii=False).encode(
            "utf-8"
        )
        subject = turn_subject(user_id)
        try:
            await asyncio.wait_for(
                self._js.publish(subject, body), timeout=timeout_seconds
            )
        except (NatsTimeoutError, APIError, asyncio.TimeoutError) as exc:
            logger.warning("publish to %s failed: %s", subject, exc)
            raise
        return turn_id

    async def aclose(self) -> None:
        if self._nc is not None:
            try:
                await self._nc.drain()
            except Exception:  # noqa: BLE001 — best-effort shutdown
                pass
            self._nc = None
            self._js = None
