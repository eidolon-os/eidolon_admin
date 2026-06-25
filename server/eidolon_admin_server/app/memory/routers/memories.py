"""Memory list / search read endpoints (writes live in writes.py)."""
from __future__ import annotations

from fastapi import APIRouter, Query, Request

from ..mcp_client import call_tool
from ..schemas import MemoryListResponse, MemorySearchResponse
from ..space import memory_actor_context_for_user

router = APIRouter()


@router.get("/memories/search", response_model=MemorySearchResponse)
async def search_memories(
    request: Request,
    user_id: str = Query(...),
    query: str = Query(..., min_length=1),
    top_k: int = Query(8, ge=1, le=100),
    wing: str | None = None,
    room: str | None = None,
    companion_id: str | None = None,
) -> MemorySearchResponse:
    context = await memory_actor_context_for_user(
        request,
        user_id,
        companion_id=companion_id,
    )
    args: dict[str, object] = {
        "query": query,
        "context": context.model_dump(mode="json"),
        "top_k": top_k,
    }
    if wing:
        args["wing"] = wing
    if room:
        args["room"] = room
    result = await call_tool(user_id, "eidolon_memory_search", args)
    records = result if isinstance(result, list) else (result or {}).get("records", [])
    return MemorySearchResponse(records=records or [])


@router.get("/memories", response_model=MemoryListResponse)
async def list_memories(
    user_id: str = Query(...),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    include_private: bool = False,
) -> MemoryListResponse:
    result = await call_tool(
        user_id,
        "eidolon_memory_list",
        {"limit": limit, "offset": offset, "include_private": include_private},
    )
    if isinstance(result, dict):
        return MemoryListResponse(
            records=result.get("records") or [],
            total_hint=int(result.get("total_hint") or 0),
        )
    if isinstance(result, list):
        return MemoryListResponse(records=result, total_hint=len(result))
    return MemoryListResponse()
