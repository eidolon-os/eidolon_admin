"""Palace hierarchy snapshot."""
from __future__ import annotations

from fastapi import APIRouter, Query

from ..mcp_client import call_tool
from ..schemas import HierarchyResponse

router = APIRouter()


@router.get("/hierarchy", response_model=HierarchyResponse)
async def get_hierarchy(
    user_id: str = Query(...),
    max_records: int = Query(8000, ge=1, le=200000),
    max_drawers_per_room: int = Query(48, ge=1, le=1000),
) -> HierarchyResponse:
    data = await call_tool(
        user_id,
        "eidolon_memory_hierarchy_snapshot",
        {"max_records": max_records, "max_drawers_per_room": max_drawers_per_room},
    )
    return HierarchyResponse(data=data if isinstance(data, dict) else {"raw": data})
