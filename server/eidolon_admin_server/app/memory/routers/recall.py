"""Recall context (vector + KG fusion).

POST shape mirrors memory: body has query/top_k/voice/include_kg/include_sensitive_kg
and user_id is a query parameter. Read-like, no NATS / yaml side effects.
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from ..mcp_client import call_tool
from ..schemas import KgTripleOut, RecallRequest, RecallResponse

router = APIRouter()


@router.post("/recall", response_model=RecallResponse)
async def recall(body: RecallRequest, user_id: str = Query(...)) -> RecallResponse:
    args = body.model_dump(exclude_none=True)
    result = await call_tool(user_id, "eidolon_memory_recall_context", args)
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
