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
from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import StreamingResponse

from .blackboard import read_runtime_blackboard
from .replay import replay_events
from .schemas import RuntimeBlackboardResponse, RuntimeEvent, RuntimeSnapshot
from .service import (
    _as_utc,
    _events_from_data,
    build_snapshot,
    enrich_runtime_event,
    hub_event_to_runtime,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mission-control", tags=["mission-control"])


@router.get("/snapshot", response_model=RuntimeSnapshot)
async def snapshot(
    request: Request,
    owner_id: str | None = Query(default=None),
    mode: str | None = Query(default=None),
) -> RuntimeSnapshot:
    return await build_snapshot(request, owner_id=owner_id, demo_mode="replay" if mode == "replay" else "live")


@router.get("/runtime-blackboard", response_model=RuntimeBlackboardResponse)
async def runtime_blackboard(
    request: Request,
    owner_id: str | None = Query(default=None, min_length=1, max_length=64),
) -> RuntimeBlackboardResponse:
    """Return the Hub-owned owner/current snapshots without projecting fields."""
    return await read_runtime_blackboard(request, owner_id=owner_id)


@router.get("/events")
async def events(
    request: Request,
    owner_id: str | None = Query(default=None),
    mode: str | None = Query(default=None),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    """The runtime stream, resumable.

    ``Last-Event-ID`` is the protocol's own cursor: a browser remembers the last
    stamped frame and sends it back on reconnect, so a dropped connection
    resumes where the reader stopped. Before this the tail started at "now" and
    everything that happened while the connection was down was gone from the
    stream — the periodic snapshot was the only backstop, and a snapshot is a
    state, not the events that produced it.
    """

    return StreamingResponse(
        _runtime_event_stream(request, owner_id, mode, since=_cursor(last_event_id)),
        media_type="text/event-stream",
    )


def _cursor(last_event_id: str | None) -> datetime | None:
    """Read a resume point this stream issued, or start from now.

    Unparseable is treated as absent rather than refused: a reader with a cursor
    from another version of this stream should get a live connection, not an
    error it cannot act on. What it loses is the gap — which is what it would
    have lost anyway.
    """

    if not (last_event_id or "").strip():
        return None
    try:
        parsed = datetime.fromisoformat(last_event_id.strip())
    except ValueError:
        return None
    return _as_utc(parsed)


async def _runtime_event_stream(
    request: Request,
    owner_id: str | None,
    mode: str | None = None,
    *,
    since: datetime | None = None,
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
        async for item in _hub_stream(request, owner_id):
            await queue.put(item)

    async def _pump_events() -> None:
        async for event, position in _events_tail(request, owner_id, since=since):
            # Only the audit tail stamps an id, because it is the only source
            # with a position to resume from. Stamping a proxied Hub frame or a
            # keepalive would move the reader's resume point to something it
            # cannot resume from.
            await queue.put(
                encode_sse_event(
                    "runtime_event",
                    event.model_dump(mode="json"),
                    event_id=position.isoformat() if position is not None else None,
                )
            )

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
) -> AsyncIterator[tuple[RuntimeEvent, datetime | None]]:
    """Cursor tail of owner-scoped audit events → live RuntimeEvents.

    Cross-process near-real-time without a bus: poll the shared events table by
    created_at cursor. ``since`` is where a reconnecting reader stopped; without
    one the tail starts at now, which is right for a first connection and wrong
    for a resumed one — the events in between would exist nowhere in the stream.

    Yields each event with its position so the frame can be stamped. Best-effort:
    a failed poll never kills the stream, and at-least-once is the contract —
    a resumed reader may see a frame twice and dedupes by ``event_id``.
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
                yield await enrich_runtime_event(request, event), row_ts
        if len(seen) > 2048:
            seen.clear()  # cursor advanced past all yielded rows; a re-send is client-deduped
        await asyncio.sleep(interval)


async def _hub_stream(request: Request, owner_id: str | None = None) -> AsyncIterator[bytes]:
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
                    event = await enrich_runtime_event(request, event)
                    if not _event_in_owner_scope(event, owner_id):
                        continue
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


def _event_in_owner_scope(event: RuntimeEvent, owner_id: str | None) -> bool:
    """Keep a selected-owner stream isolated from Hub's global event feed.

    Device-backed frames are enriched from the authoritative binding before
    this check. Unattributed global frames remain available only on an
    unscoped stream; assigning them to whichever owner happens to be selected
    would fabricate ownership.
    """

    return owner_id is None or event.owner_id == owner_id


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
