"""Knowledge graph + Palace graph snapshots for visualization.

memory exposes two MCP tools with intentionally different shapes:

- ``eidolon_memory_palace_graph``  → already node/edge shape (cross-wing
                                     tunnel graph). Pass-through.
- ``eidolon_memory_kg_snapshot``   → returns ``{stats, triples}``; admin is
                                     responsible for projecting triples into
                                     node/edge form for visualisation.

The KG projector mirrors memory's legacy admin (graph_service._triples_to_graph)
so the UX stays consistent through the migration.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from ..mcp_client import call_tool
from ..schemas import GraphEdgeOut, GraphNodeOut, GraphSnapshot

router = APIRouter()


def _entity_type(entity_id: str) -> str:
    """Parse the conventional ``<type>:<value>`` prefix used in KG entity ids.

    e.g. ``pet:铁锤`` → ``pet``, ``place:北京`` → ``place``,
    ``self`` → ``self``, ``alice`` → ``person`` (heuristic).
    """
    if not entity_id:
        return ""
    if entity_id == "self":
        return "self"
    if ":" in entity_id:
        return entity_id.split(":", 1)[0]
    return "person"  # bare names default to person


def _project_triples_to_graph(
    triples: list[dict[str, Any]],
    max_nodes: int,
) -> tuple[list[GraphNodeOut], list[GraphEdgeOut], bool]:
    """Build nodes+edges from KG triples. Returns (nodes, edges, capped)."""
    node_by_id: dict[str, GraphNodeOut] = {}
    edges: list[GraphEdgeOut] = []
    capped = False

    def ensure_node(entity_id: str) -> bool:
        """True if this id can be added (under cap), False if we hit the cap."""
        if entity_id in node_by_id:
            return True
        if len(node_by_id) >= max_nodes:
            return False
        node_by_id[entity_id] = GraphNodeOut(
            id=entity_id,
            label=entity_id.split(":", 1)[-1] if ":" in entity_id else entity_id,
            kind="entity",
            entity_type=_entity_type(entity_id),
        )
        return True

    for t in triples:
        subj = str(t.get("subject") or "")
        obj = str(t.get("object") or "")
        if not subj or not obj:
            continue
        if not ensure_node(subj) or not ensure_node(obj):
            capped = True
            break
        edges.append(GraphEdgeOut(
            source=subj,
            target=obj,
            label=str(t.get("predicate") or ""),
            valid_from=t.get("valid_from"),
            valid_to=t.get("valid_to"),
            current=t.get("valid_to") is None,
        ))

    return list(node_by_id.values()), edges, capped


@router.get("/graph/knowledge", response_model=GraphSnapshot)
async def knowledge_graph(
    memory_realm_id: str = Query(...),
    max_triples: int = Query(400, ge=1, le=5000),
    max_nodes: int = Query(400, ge=1, le=5000),
    current_only: bool = True,
    entity: str | None = None,
    include_sensitive: bool = False,
) -> GraphSnapshot:
    args: dict[str, object] = {
        "max_triples": max_triples,
        "current_only": current_only,
        "include_sensitive": include_sensitive,
    }
    if entity:
        args["entity"] = entity
    payload = await call_tool(memory_realm_id, "eidolon_memory_kg_snapshot", args)
    if not isinstance(payload, dict):
        return GraphSnapshot(available=False, reason="unexpected payload shape")
    triples = payload.get("triples") or []
    nodes, edges, capped = _project_triples_to_graph(triples, max_nodes=max_nodes)
    stats = payload.get("stats") or {}
    return GraphSnapshot(
        available=True,
        palace_path=str(payload.get("palace_path") or ""),
        nodes=nodes,
        edges=edges,
        capped=capped,
        reason=(
            f"{len(triples)} triples (active={stats.get('triples_active', '?')}, "
            f"invalid={stats.get('triples_invalidated', '?')})"
        ),
    )


@router.get("/graph/palace", response_model=GraphSnapshot)
async def palace_graph(
    memory_realm_id: str = Query(...),
    max_nodes: int = Query(120, ge=1, le=2000),
    max_edges: int = Query(200, ge=1, le=5000),
) -> GraphSnapshot:
    # palace_graph tool already returns {nodes, edges, ...} directly.
    payload = await call_tool(
        memory_realm_id,
        "eidolon_memory_palace_graph",
        {"max_nodes": max_nodes, "max_edges": max_edges},
    )
    if not isinstance(payload, dict):
        return GraphSnapshot(available=False, reason="unexpected payload shape")
    return GraphSnapshot.model_validate(payload)
