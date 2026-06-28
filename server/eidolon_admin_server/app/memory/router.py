"""Memory module — top-level router.

Mounts every sub-router under ``/memory`` so the URL space stays:

    /api/memory/runners
    /api/memory/realms
    /api/memory/memories[/search]
    /api/memory/hierarchy
    /api/memory/graph/{knowledge,palace}
    /api/memory/kg/...
    /api/memory/recall
    /api/memory/mcp/tools
    /api/memory/realms/{memory_realm_id}/rebuild-index

Each sub-router lives in ``memory/routers/<feature>.py`` and stays small
(< 80 lines). Pydantic models are centralised in ``schemas.py``.
"""
from __future__ import annotations

from fastapi import APIRouter

from .routers import (
    graph,
    hierarchy,
    kg,
    maintenance,
    mcp_tools,
    memories,
    realms,
    recall,
    runners,
    writes,
)

router = APIRouter(prefix="/memory", tags=["memory"])

# Read surface (Phase 13).
router.include_router(runners.router)
router.include_router(realms.router)
router.include_router(memories.router)
router.include_router(hierarchy.router)
router.include_router(graph.router)
router.include_router(kg.router)
router.include_router(recall.router)
router.include_router(mcp_tools.router)

# Write surface (Phase 14).
router.include_router(writes.router)

router.include_router(maintenance.router)
