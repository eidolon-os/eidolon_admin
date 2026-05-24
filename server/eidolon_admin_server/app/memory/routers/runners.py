"""Per-user agent_runner discovery (moved from memory/router.py)."""
from __future__ import annotations

from fastapi import APIRouter

from ..runners import list_runners

router = APIRouter()


@router.get("/runners")
async def get_runners() -> dict:
    return await list_runners()
