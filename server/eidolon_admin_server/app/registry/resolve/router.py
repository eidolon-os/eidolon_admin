"""FastAPI router for ``/api/resolve/*`` — aggregator endpoints.

Two routes:
    GET /api/resolve/device/{device_id}  → ResolveDeviceResponse
    GET /api/resolve/user/{user_id}      → ResolveUserResponse

Both return a ``ResolvedContext`` envelope. No mutation; no upsert
behaviour. The router does not have its own "envelope when down"
mode — runtime callers depend on these resolving cleanly OR getting
a clear error to react to.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..schemas.resolve import ResolveDeviceResponse, ResolveUserResponse
from .orchestrator import ResolveError, ResolveOrchestrator

router = APIRouter(prefix="/resolve", tags=["resolve"])


def _orchestrator(request: Request) -> ResolveOrchestrator:
    orch: ResolveOrchestrator | None = getattr(
        request.app.state, "resolve_orchestrator", None
    )
    if orch is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "resolve orchestrator unavailable — admin booted without "
                "the full registry (Tenants/Templates/Users/Agents/Devices) "
                "initialized"
            ),
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


@router.get("/user/{user_id}", response_model=ResolveUserResponse)
async def resolve_user(user_id: str, request: Request) -> ResolveUserResponse:
    orch = _orchestrator(request)
    try:
        context = await orch.resolve_user(user_id)
    except ResolveError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return ResolveUserResponse(context=context)
