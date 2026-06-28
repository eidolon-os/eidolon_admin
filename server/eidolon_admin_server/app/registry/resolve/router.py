"""FastAPI router for device-bound runtime identity resolution."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..schemas.resolve import ResolveDeviceResponse
from .orchestrator import ResolveError, ResolveOrchestrator

router = APIRouter(prefix="/resolve", tags=["resolve"])


def _orchestrator(request: Request) -> ResolveOrchestrator:
    orch: ResolveOrchestrator | None = getattr(
        request.app.state, "resolve_orchestrator", None
    )
    if orch is None:
        raise HTTPException(
            status_code=503,
            detail="resolve orchestrator unavailable",
        )
    return orch


@router.get("/device/{device_id}", response_model=ResolveDeviceResponse)
async def resolve_device(device_id: str, request: Request) -> ResolveDeviceResponse:
    orch = _orchestrator(request)
    try:
        context = await orch.resolve_device(device_id)
    except ResolveError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return ResolveDeviceResponse(context=context)
