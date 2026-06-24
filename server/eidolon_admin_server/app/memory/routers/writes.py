"""Write endpoints for memory (Phase 14).

Three writes total:

  POST /api/memory/memories            -> NATS publish ConversationTurnPayload
  POST /api/memory/kg/triples          -> MCP tool eidolon_memory_kg_add_triple
  POST /api/memory/kg/invalidations    -> MCP tool eidolon_memory_kg_invalidate

The MCP tools internally publish KG commands to NATS; we just relay request →
tool call → typed response.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from eidolon_sdk.memory import MemoryActorContext, conversation_turn_subject

from ..mcp_client import call_tool
from ..space import DEFAULT_PERSONA_ID, tenant_for_user
from ..schemas import (
    KgInvalidateRequest,
    KgTripleAddRequest,
    KgWriteResult,
    MemoryCreateRequest,
    MemoryWriteAccepted,
)

router = APIRouter()

# This manual-injection path has no real agent/device/instance/session; tag the
# actor provenance honestly so downstream can distinguish UI writes.
_ADMIN_UI_SENTINEL = "admin_ui"


@router.post(
    "/memories",
    response_model=MemoryWriteAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_memory(body: MemoryCreateRequest, request: Request) -> MemoryWriteAccepted:
    publisher = request.app.state.memory_publisher
    if publisher is None:
        raise HTTPException(503, "memory NATS publisher not initialised")
    tenant_id = await tenant_for_user(request, body.user_id)
    try:
        context = MemoryActorContext(
            tenant_id=tenant_id,
            owner_user_id=body.user_id,
            persona_id=DEFAULT_PERSONA_ID,
            agent_id=_ADMIN_UI_SENTINEL,
            device_id=_ADMIN_UI_SENTINEL,
            instance_id=_ADMIN_UI_SENTINEL,
            session_id=_ADMIN_UI_SENTINEL,
        )
    except ValueError as exc:
        raise HTTPException(422, f"invalid memory actor context: {exc}") from exc
    try:
        turn_id = await publisher.publish_turn(
            context=context,
            user_text=body.text,
            assistant_text="",
            metadata={
                "wing": body.wing,
                "room": body.room,
                "source": "admin_ui",
                **body.metadata,
            },
        )
    except Exception as exc:  # noqa: BLE001 — wrap any NATS failure
        raise HTTPException(502, f"NATS publish failed: {exc}") from exc
    subject = conversation_turn_subject(context.memory_space_id)
    return MemoryWriteAccepted(
        detail=f"Published to {subject}; turn_id={turn_id}",
    )


def _kg_result(payload: object) -> KgWriteResult:
    if isinstance(payload, dict):
        return KgWriteResult(
            status=str(payload.get("status") or "pending"),
            request_id=payload.get("request_id"),
            triple_id=payload.get("triple_id"),
        )
    return KgWriteResult(status="pending")


@router.post(
    "/kg/triples",
    response_model=KgWriteResult,
    status_code=status.HTTP_202_ACCEPTED,
)
async def add_kg_triple(body: KgTripleAddRequest) -> KgWriteResult:
    args = body.model_dump(exclude={"user_id"}, exclude_none=True)
    payload = await call_tool(body.user_id, "eidolon_memory_kg_add_triple", args)
    return _kg_result(payload)


@router.post(
    "/kg/invalidations",
    response_model=KgWriteResult,
    status_code=status.HTTP_202_ACCEPTED,
)
async def invalidate_kg(body: KgInvalidateRequest) -> KgWriteResult:
    args = body.model_dump(exclude={"user_id"}, exclude_none=True)
    payload = await call_tool(body.user_id, "eidolon_memory_kg_invalidate", args)
    return _kg_result(payload)
