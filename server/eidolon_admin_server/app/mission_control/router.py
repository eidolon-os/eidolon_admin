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

from .replay import replay_events
from .schemas import RuntimeEvent, RuntimeSnapshot
from .service import _as_utc, _events_from_data, build_snapshot, hub_event_to_runtime

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mission-control", tags=["mission-control"])


@router.get("/snapshot", response_model=RuntimeSnapshot)
async def snapshot(
    request: Request,
    owner_id: str | None = Query(default=None),
    mode: str | None = Query(default=None),
) -> RuntimeSnapshot:
    return await build_snapshot(request, owner_id=owner_id, demo_mode="replay" if mode == "replay" else "live")


@router.get("/events")
async def events(
    request: Request,
    owner_id: str | None = Query(default=None),
    mode: str | None = Query(default=None),
) -> StreamingResponse:
    return StreamingResponse(_runtime_event_stream(request, owner_id, mode), media_type="text/event-stream")


async def _runtime_event_stream(
    request: Request,
    owner_id: str | None,
    mode: str | None = None,
) -> AsyncIterator[bytes]:
    if mode == "replay":
        # Recorded demo playback — never touches Hub; every frame is replay-origin.
        yield encode_sse_event("runtime_event", _startup_event(origin="replay").model_dump(mode="json"))
        async for event in replay_events(request.is_disconnected):
            yield encode_sse_event("runtime_event", event.model_dump(mode="json"))
        return
    yield encode_sse_event("runtime_event", _startup_event().model_dump(mode="json"))

    # Merge two live sources into one SSE: the proxied Hub device stream and a
    # cursor tail of owner-scoped audit events from the shared DB (cross-process,
    # no bus coupling — every service writes the same events table). Each source
    # runs as a task feeding one queue; both are cancelled on disconnect.
    queue: asyncio.Queue[bytes] = asyncio.Queue()

    async def _pump_hub() -> None:
        async for item in _hub_stream(request):
            await queue.put(item)

    async def _pump_events() -> None:
        async for event in _events_tail(request, owner_id):
            await queue.put(encode_sse_event("runtime_event", event.model_dump(mode="json")))

    tasks = [asyncio.create_task(_pump_hub()), asyncio.create_task(_pump_events())]
    try:
        while not await request.is_disconnected():
            try:
                item = await asyncio.wait_for(queue.get(), timeout=5.0)
            except TimeoutError:
                yield encode_sse_comment("keepalive")
                continue
            yield item
    finally:
        for task in tasks:
            task.cancel()


async def _events_tail(
    request: Request,
    owner_id: str | None,
    *,
    interval: float = 1.5,
    since: datetime | None = None,
) -> AsyncIterator[RuntimeEvent]:
    """Cursor tail of owner-scoped audit events → live RuntimeEvents.

    Cross-process near-real-time without a bus: poll the shared events table by
    created_at cursor. Starts at 'now' so only NEW events stream (the periodic
    snapshot carries history and is the miss-backstop). Best-effort: a failed poll
    never kills the stream; callers dedupe by event_id.
    """
    app = getattr(request, "app", None)
    state = getattr(app, "state", None)
    store = getattr(state, "data_store", None)
    if store is None or not owner_id:
        return
    # aware-UTC throughout; SQLite reads created_at back naive, so normalize before
    # comparing (SQLAlchemy strips tz when binding the query param, so SQL is fine).
    cursor = _as_utc(since) if since is not None else datetime.now(UTC)
    seen: set[str] = set()
    while not await request.is_disconnected():
        try:
            rows = await store.events.list_for_owner_since(owner_id, after=cursor, limit=200)
        except Exception:  # noqa: BLE001 - a bad poll must not kill the stream
            rows = []
        for row in rows:
            if row.event_id in seen:
                continue
            seen.add(row.event_id)
            row_ts = _as_utc(row.created_at)
            if row_ts is not None and row_ts > cursor:
                cursor = row_ts
            for event in _events_from_data([row]):
                yield event
        if len(seen) > 2048:
            seen.clear()  # cursor advanced past all yielded rows; a re-send is client-deduped
        await asyncio.sleep(interval)


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
                event_origin="live",
                summary=f"Hub stream unavailable: {exc}",
                payload={},
            )
            yield encode_sse_event("runtime_event", event.model_dump(mode="json"))
            await asyncio.sleep(3)


def _startup_event(origin: str = "live") -> RuntimeEvent:
    return RuntimeEvent(
        event_id=f"mc-start-{uuid4().hex}",
        ts=datetime.now(UTC),
        source="mission_control",
        type="mission_control.connected",
        severity="info",
        event_origin=origin,  # type: ignore[arg-type]
        summary="Mission Control event stream connected",
        payload={},
    )
