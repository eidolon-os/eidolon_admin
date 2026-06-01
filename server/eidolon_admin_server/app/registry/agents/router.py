"""FastAPI router for ``/api/agents/*``.

HTTP I/O only. Matches the tenants/templates/users pattern.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from ..schemas.agent import (
    AgentDetail,
    AgentListResponse,
    AgentRef,
    CreateAgentRequest,
)
from .orchestrator import AgentError, AgentOrchestrator

router = APIRouter(prefix="/agents", tags=["agents"])


def _orchestrator(request: Request) -> AgentOrchestrator:
    orch: AgentOrchestrator | None = getattr(
        request.app.state, "agent_orchestrator", None
    )
    if orch is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "agent orchestrator unavailable — admin booted without "
                "the agent or memory service URLs, or NATS is down"
            ),
        )
    return orch


@router.get("", response_model=AgentListResponse)
async def list_agents(
    request: Request,
    user_id: str | None = Query(default=None, description="Filter by owning user"),
) -> AgentListResponse:
    orch = _orchestrator(request)
    try:
        agents = await orch.list_agents(user_id=user_id)
        return AgentListResponse(agents=agents, upstream_available=True)
    except AgentError as exc:
        if exc.status_code == 503:
            return AgentListResponse(agents=[], upstream_available=False)
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("/{agent_id}", response_model=AgentDetail)
async def get_agent(agent_id: str, request: Request) -> AgentDetail:
    orch = _orchestrator(request)
    try:
        return await orch.get_agent(agent_id)
    except AgentError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("", response_model=AgentRef, status_code=201)
async def create_agent(body: CreateAgentRequest, request: Request) -> AgentRef:
    orch = _orchestrator(request)
    try:
        return await orch.create_agent(body)
    except AgentError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.delete("/{agent_id}", status_code=200)
async def delete_agent(agent_id: str, request: Request) -> dict:
    orch = _orchestrator(request)
    try:
        return await orch.delete_agent(agent_id)
    except AgentError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("/{agent_id}/evolution")
async def get_evolution(
    agent_id: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
) -> list[dict]:
    orch = _orchestrator(request)
    try:
        return await orch.get_evolution_history(agent_id, limit=limit)
    except AgentError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
