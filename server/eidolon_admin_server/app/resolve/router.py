"""FastAPI router for runtime identity resolution."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from .orchestrator import ResolveError, ResolveOrchestrator
from .schemas import ResolveResponse

router = APIRouter(prefix="/resolve", tags=["resolve"])


def _orchestrator(request: Request) -> ResolveOrchestrator:
    orch: ResolveOrchestrator | None = getattr(
        request.app.state, "resolve_orchestrator", None
    )
    if orch is None:
        raise HTTPException(status_code=503, detail="resolve orchestrator unavailable")
    return orch


@router.get("/owner/{owner_id}", response_model=ResolveResponse)
async def resolve_owner(owner_id: str, request: Request) -> ResolveResponse:
    try:
        context = await _orchestrator(request).resolve_owner(owner_id)
    except ResolveError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return ResolveResponse(context=context)


@router.get("/device/{device_id}", response_model=ResolveResponse)
async def resolve_device(device_id: str, request: Request) -> ResolveResponse:
    try:
        context = await _orchestrator(request).resolve_device(device_id)
    except ResolveError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return ResolveResponse(context=context)
