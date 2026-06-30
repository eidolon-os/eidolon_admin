"""Mission Control API surface."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

import httpx
from eidolon_sdk.core.streaming import encode_sse_comment, encode_sse_event
from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from .schemas import RuntimeEvent, RuntimeSnapshot
from .service import build_snapshot, hub_event_to_runtime

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mission-control", tags=["mission-control"])


@router.get("/snapshot", response_model=RuntimeSnapshot)
async def snapshot(
    request: Request,
    owner_id: str | None = Query(default=None),
) -> RuntimeSnapshot:
    return await build_snapshot(request, owner_id=owner_id)


@router.get("/events")
async def events(
    request: Request,
    owner_id: str | None = Query(default=None),
) -> StreamingResponse:
    return StreamingResponse(_runtime_event_stream(request, owner_id), media_type="text/event-stream")


async def _runtime_event_stream(
    request: Request,
    owner_id: str | None,
) -> AsyncIterator[bytes]:
    _ = owner_id  # Reserved for Phase 2 owner-scoped NATS subscriptions.
    yield encode_sse_event("runtime_event", _startup_event().model_dump(mode="json"))
    async for item in _hub_stream(request):
        if await request.is_disconnected():
            break
        yield item


async def _hub_stream(request: Request) -> AsyncIterator[bytes]:
    registry = getattr(request.app.state, "registry", None)
    http_client: httpx.AsyncClient | None = getattr(request.app.state, "http_client", None)
    service = registry.get("hub") if registry is not None else None
    if http_client is None or service is None or not service.base_url:
        while not await request.is_disconnected():
            yield encode_sse_comment("hub unavailable")
            await asyncio.sleep(5)
        return

    url = f"{service.base_url.rstrip('/')}/api/admin/stream/events"
    while not await request.is_disconnected():
        try:
            async with http_client.stream("GET", url, timeout=None) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if await request.is_disconnected():
                        return
                    if not line:
                        continue
                    if line.startswith(":"):
                        yield encode_sse_comment("hub keepalive")
                        continue
                    if not line.startswith("data:"):
                        continue
                    raw_text = line.removeprefix("data:").strip()
                    if not raw_text or raw_text == "{}":
                        yield encode_sse_comment("hub ping")
                        continue
                    try:
                        raw = json.loads(raw_text)
                    except ValueError:
                        logger.debug("Mission Control ignored malformed Hub SSE frame")
                        continue
                    event = hub_event_to_runtime(raw if isinstance(raw, dict) else {"payload": raw})
                    yield encode_sse_event("runtime_event", event.model_dump(mode="json"))
        except Exception as exc:  # noqa: BLE001 - SSE should reconnect forever
            event = RuntimeEvent(
                event_id=f"mc-sse-{uuid4().hex}",
                ts=datetime.now(UTC),
                source="mission_control",
                type="source.hub.degraded",
                severity="warn",
                summary=f"Hub stream unavailable: {exc}",
                payload={},
            )
            yield encode_sse_event("runtime_event", event.model_dump(mode="json"))
            await asyncio.sleep(3)


def _startup_event() -> RuntimeEvent:
    return RuntimeEvent(
        event_id=f"mc-start-{uuid4().hex}",
        ts=datetime.now(UTC),
        source="mission_control",
        type="mission_control.connected",
        severity="info",
        summary="Mission Control event stream connected",
        payload={},
    )
