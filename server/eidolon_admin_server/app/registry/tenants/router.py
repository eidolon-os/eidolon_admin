"""FastAPI router for ``/api/tenants/*``.

Pattern matches ``devices/router.py``: HTTP I/O only, all business logic
delegated to :class:`TenantOrchestrator`. Errors raised by the orchestrator
map to HTTP status codes via ``TenantError.status_code``.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..schemas.tenant import (
    CreateTenantRequest,
    TenantListResponse,
    TenantSpec,
    UpdateTenantRequest,
)
from .orchestrator import TenantError, TenantOrchestrator

router = APIRouter(prefix="/tenants", tags=["tenants"])


def _orchestrator(request: Request) -> TenantOrchestrator:
    orch: TenantOrchestrator | None = getattr(
        request.app.state, "tenant_orchestrator", None
    )
    if orch is None:
        # Admin booted but the local registry layer isn't initialized.
        raise HTTPException(
            status_code=503,
            detail=(
                "tenant orchestrator unavailable (registry init failed) — "
                "check admin startup log"
            ),
        )
    return orch


@router.get("", response_model=TenantListResponse)
async def list_tenants(request: Request) -> TenantListResponse:
    orch = _orchestrator(request)
    tenants = await orch.list_all()
    return TenantListResponse(tenants=tenants)


@router.get("/{tenant_id}", response_model=TenantSpec)
async def get_tenant(tenant_id: str, request: Request) -> TenantSpec:
    orch = _orchestrator(request)
    try:
        return await orch.get(tenant_id)
    except TenantError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("", response_model=TenantSpec, status_code=201)
async def create_tenant(body: CreateTenantRequest, request: Request) -> TenantSpec:
    orch = _orchestrator(request)
    try:
        return await orch.create(body)
    except TenantError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.put("/{tenant_id}", response_model=TenantSpec)
async def update_tenant(
    tenant_id: str, body: UpdateTenantRequest, request: Request
) -> TenantSpec:
    orch = _orchestrator(request)
    try:
        return await orch.update(tenant_id, body)
    except TenantError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.delete("/{tenant_id}", status_code=204)
async def delete_tenant(tenant_id: str, request: Request) -> None:
    orch = _orchestrator(request)
    try:
        await orch.delete(tenant_id)
    except TenantError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    # 204 — no body
