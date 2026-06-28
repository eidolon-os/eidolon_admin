"""Knowledge graph read endpoints (predicates / stats / entity / timeline).

KG writes (add_triple / invalidate) live in writes.py.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from ..mcp_client import call_tool
from ..schemas import (
    KgEntityResponse,
    KgPredicates,
    KgStats,
    KgTimelineResponse,
    KgTripleOut,
)

router = APIRouter()


def _triples(data: Any) -> list[KgTripleOut]:
    if not isinstance(data, list):
        return []
    return [KgTripleOut.model_validate(t) for t in data if isinstance(t, dict)]


@router.get("/kg/predicates", response_model=KgPredicates)
async def kg_predicates(memory_realm_id: str = Query(...)) -> KgPredicates:
    result = await call_tool(memory_realm_id, "eidolon_memory_kg_predicates")
    if isinstance(result, dict):
        return KgPredicates(
            predicates=list(result.get("predicates") or []),
            sensitive=list(result.get("sensitive") or []),
        )
    return KgPredicates(predicates=list(result or []))


@router.get("/kg/stats", response_model=KgStats)
async def kg_stats(memory_realm_id: str = Query(...)) -> KgStats:
    result = await call_tool(memory_realm_id, "eidolon_memory_kg_stats")
    if isinstance(result, dict):
        return KgStats.model_validate(result)
    return KgStats()


@router.get("/kg/entity/{name}", response_model=KgEntityResponse)
async def kg_entity(
    name: str,
    memory_realm_id: str = Query(...),
    as_of: str | None = None,
    direction: str = Query("outgoing", pattern="^(outgoing|incoming|both)$"),
    include_sensitive: bool = False,
) -> KgEntityResponse:
    args: dict[str, Any] = {
        "entity": name,
        "direction": direction,
        "include_sensitive": include_sensitive,
    }
    if as_of:
        args["as_of"] = as_of
    result = await call_tool(memory_realm_id, "eidolon_memory_kg_query_entity", args)
    triples = _triples(result.get("triples") if isinstance(result, dict) else result)
    return KgEntityResponse(entity=name, triples=triples)


@router.get("/kg/timeline", response_model=KgTimelineResponse)
async def kg_timeline(
    memory_realm_id: str = Query(...),
    entity_name: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
    include_sensitive: bool = False,
) -> KgTimelineResponse:
    args: dict[str, Any] = {"limit": limit, "include_sensitive": include_sensitive}
    if entity_name:
        args["entity_name"] = entity_name
    if since:
        args["since"] = since
    if until:
        args["until"] = until
    result = await call_tool(memory_realm_id, "eidolon_memory_kg_timeline", args)
    triples = _triples(result.get("triples") if isinstance(result, dict) else result)
    return KgTimelineResponse(triples=triples)
