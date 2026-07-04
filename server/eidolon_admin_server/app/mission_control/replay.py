"""Replay mode for Mission Control — plays recorded demo fixtures as a runtime
event stream so a demo can be shown end-to-end without live hardware. Every
emitted frame is stamped ``event_origin="replay"`` and NEVER touches Hub, so it
is always honestly distinguishable from live telemetry."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .schemas import RuntimeEvent

_FIXTURE = Path(__file__).parent / "fixtures" / "mission_control_demo.json"


def _load_frames() -> list[dict[str, Any]]:
    try:
        data = json.loads(_FIXTURE.read_text("utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _frame_to_event(frame: dict[str, Any]) -> RuntimeEvent:
    return RuntimeEvent(
        event_id=f"replay-{uuid4().hex}",
        ts=datetime.now(UTC),
        source=frame.get("source", "mission_control"),
        type=str(frame.get("type", "replay.event")),
        severity=frame.get("severity", "info"),
        event_origin="replay",
        device_id=frame.get("device_id"),
        companion_id=frame.get("companion_id"),
        turn_id=frame.get("turn_id"),
        job_id=frame.get("job_id"),
        summary=str(frame.get("summary", "")),
        payload=frame.get("payload") or {},
    )


async def replay_events(is_disconnected: Callable[[], Awaitable[bool]]) -> AsyncIterator[RuntimeEvent]:
    """Loop the fixture as a paced event stream until the client disconnects."""
    frames = _load_frames()
    if not frames:
        return
    while not await is_disconnected():
        for frame in frames:
            if await is_disconnected():
                return
            yield _frame_to_event(frame)
            await asyncio.sleep(float(frame.get("delay", 1.2)))
        await asyncio.sleep(2.0)
