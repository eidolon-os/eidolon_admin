"""Recall context (vector + KG fusion).

POST shape mirrors memory: body has query/top_k/voice/include_kg/include_sensitive_kg
and memory_realm_id is a query parameter. Read-like, no NATS / yaml side effects.
"""
from __future__ import annotations

from fastapi import APIRouter, Query, Request

from ..mcp_client import call_tool
from ..schemas import KgTripleOut, RecallRequest, RecallResponse
from ..space import memory_actor_context_for_realm

router = APIRouter()


@router.post("/recall", response_model=RecallResponse)
async def recall(
    body: RecallRequest,
    request: Request,
    memory_realm_id: str = Query(...),
) -> RecallResponse:
    args = body.model_dump(exclude_none=True)
    context = await memory_actor_context_for_realm(
        request,
        memory_realm_id,
    )
    args["context"] = context.model_dump(mode="json")
    result = await call_tool(memory_realm_id, "eidolon_memory_recall_context", args)
    if not isinstance(result, dict):
        return RecallResponse(context=str(result) if result else "")
    triples_raw = result.get("kg_triples") or []
    return RecallResponse(
        context=str(result.get("context") or ""),
        kg_triples=[
            KgTripleOut.model_validate(t) for t in triples_raw if isinstance(t, dict)
        ],
        records=list(result.get("records") or []),
    )
