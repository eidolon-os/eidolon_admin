"""FastAPI router for ``/api/templates/*``.

Pattern matches ``tenants/router.py`` — HTTP I/O only, all business
logic delegated to :class:`TemplateOrchestrator`. The big difference:
errors here may originate from the *agent* project's REST surface (via
the repository) — the orchestrator already mapped them to admin's
exception classes, so this layer is uniform.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..schemas.template import (
    CreateTemplateRequest,
    ForkTemplateRequest,
    TemplateDetail,
    TemplateListResponse,
    TemplateRef,
    UpdateTemplateRequest,
)
from .orchestrator import TemplateError, TemplateOrchestrator

router = APIRouter(prefix="/templates", tags=["templates"])


def _orchestrator(request: Request) -> TemplateOrchestrator:
    orch: TemplateOrchestrator | None = getattr(
        request.app.state, "template_orchestrator", None
    )
    if orch is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "template orchestrator unavailable — agent service URL "
                "missing from services.yaml or admin booted without it"
            ),
        )
    return orch


@router.get("", response_model=TemplateListResponse)
async def list_templates(request: Request) -> TemplateListResponse:
    orch = _orchestrator(request)
    try:
        templates = await orch.list_all()
    except TemplateError as exc:
        # If agent is unreachable (TemplateAgentDown), we still return a
        # well-formed envelope so the UI shows "agent unavailable" rather
        # than crashing. status_code on the envelope tells the truth.
        if exc.status_code == 503:
            return TemplateListResponse(templates=[], upstream_available=False)
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return TemplateListResponse(templates=templates, upstream_available=True)


@router.get("/{template_id}", response_model=TemplateDetail)
async def get_template(template_id: str, request: Request) -> TemplateDetail:
    orch = _orchestrator(request)
    try:
        return await orch.get(template_id)
    except TemplateError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("", response_model=TemplateRef, status_code=201)
async def create_template(
    body: CreateTemplateRequest, request: Request
) -> TemplateRef:
    orch = _orchestrator(request)
    try:
        return await orch.create(body)
    except TemplateError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.put("/{template_id}", response_model=TemplateRef)
async def update_template(
    template_id: str, body: UpdateTemplateRequest, request: Request
) -> TemplateRef:
    orch = _orchestrator(request)
    try:
        return await orch.update(template_id, body)
    except TemplateError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.delete("/{template_id}", status_code=204)
async def delete_template(template_id: str, request: Request) -> None:
    orch = _orchestrator(request)
    try:
        await orch.delete(template_id)
    except TemplateError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/{template_id}/fork", response_model=TemplateRef, status_code=201)
async def fork_template(
    template_id: str, body: ForkTemplateRequest, request: Request
) -> TemplateRef:
    orch = _orchestrator(request)
    try:
        return await orch.fork(template_id, body)
    except TemplateError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
