"""REST surface for the Devices module.

The router does only three things, in order, per endpoint:
  1. validate inputs (Pydantic + a couple of explicit checks)
  2. look up dependencies from app state
  3. call exactly one orchestrator method

Business logic is forbidden here. If you find yourself writing an `if` that
isn't about HTTP shape (status code mapping, missing dependency) it belongs
in the orchestrator instead.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from .orchestrator import (
    AgentNotFound,
    CompensationFailed,
    DeviceNotApproved,
    DeviceNotFound,
    DeviceOrchestrator,
    HubUnreachable,
    OrchestratorError,
    SoulTooLarge,
    TemplateRenderFailed,
)
from .schemas import (
    AgentEntry,
    ApproveResponse,
    CreateAgentRequest,
    CreateAgentResponse,
    DeleteAgentResponse,
    DeviceBindingView,
    DeviceListResponse,
    DeviceView,
    SoulResponse,
    SwitchActiveAgentRequest,
    SwitchActiveResponse,
    UpdateSoulRequest,
    UpdateSoulResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/devices", tags=["devices"])


def _orchestrator(request: Request) -> DeviceOrchestrator:
    """Pull the orchestrator from app state.

    Centralised so the 503 message when NATS isn't ready is identical for
    every endpoint — operator sees a single, recognisable failure mode
    instead of differently-worded errors per route.
    """
    orch = getattr(request.app.state, "device_orchestrator", None)
    if orch is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "device orchestration unavailable — NATS connection failed at "
                "admin-api startup. Check that the nats program is RUNNING in "
                "supervisord and restart admin-api."
            ),
        )
    return orch


def _raise_for(exc: OrchestratorError) -> None:
    """Map an OrchestratorError subclass to its HTTP equivalent."""
    raise HTTPException(status_code=exc.status_code, detail=exc.message)


# ---- list -------------------------------------------------------------------


@router.get("", response_model=DeviceListResponse)
async def list_devices(request: Request) -> DeviceListResponse:
    orch = _orchestrator(request)
    try:
        composites = await orch.list_devices()
    except HubUnreachable as exc:
        _raise_for(exc)
    except OrchestratorError as exc:
        _raise_for(exc)

    devices = [
        DeviceView(
            device_id=c.device_id,
            name=c.name,
            approved=c.approved,
            approved_at=c.approved_at,
            paired=c.paired,
            enabled=c.enabled,
            last_seen=c.last_seen,
            status=c.status,
            binding=(
                DeviceBindingView(
                    user_id=c.binding.user_id,
                    agent_ids=list(c.binding.agent_ids),
                    active_agent_id=c.binding.active_agent_id,
                    updated_at=c.binding.updated_at,
                    agents=[
                        AgentEntry(
                            agent_id=a.agent_id,
                            template_id=a.template_id,
                            template_revision=a.template_revision,
                            owner_user_id=a.owner_user_id,
                            owner_device_id=a.owner_device_id,
                            created_at=a.created_at,
                            updated_at=a.updated_at,
                            is_active=a.is_active,
                        )
                        for a in c.binding.agents
                    ],
                )
                if c.binding is not None
                else None
            ),
        )
        for c in composites
    ]
    return DeviceListResponse(devices=devices, nats_available=True)


# ---- approve ----------------------------------------------------------------


@router.post("/{device_id}/approve", response_model=ApproveResponse)
async def approve_device(device_id: str, request: Request) -> ApproveResponse:
    orch = _orchestrator(request)
    try:
        payload = await orch.approve(device_id)
    except (DeviceNotFound, HubUnreachable, OrchestratorError) as exc:
        _raise_for(exc)
    return ApproveResponse(
        device_id=payload["device_id"],
        approved=payload["approved"],
        approved_at=payload.get("approved_at"),
    )


# ---- agents (create / switch active / delete) ------------------------------


@router.post("/{device_id}/agents", response_model=CreateAgentResponse)
async def create_agent(
    device_id: str, body: CreateAgentRequest, request: Request
) -> CreateAgentResponse:
    orch = _orchestrator(request)
    try:
        agent_id, preview_chars, is_active = await orch.create_agent(
            device_id=device_id,
            template_id=body.template_id,
            user_id=body.user_id,
        )
    except (
        DeviceNotApproved,
        DeviceNotFound,
        TemplateRenderFailed,
        SoulTooLarge,
        CompensationFailed,
        HubUnreachable,
        OrchestratorError,
    ) as exc:
        _raise_for(exc)
    return CreateAgentResponse(
        agent_id=agent_id,
        soul_preview_chars=preview_chars,
        is_active=is_active,
    )


@router.post("/{device_id}/active-agent", response_model=SwitchActiveResponse)
async def switch_active_agent(
    device_id: str, body: SwitchActiveAgentRequest, request: Request
) -> SwitchActiveResponse:
    orch = _orchestrator(request)
    try:
        new_active = await orch.switch_active(device_id, body.agent_id)
    except (DeviceNotFound, AgentNotFound, OrchestratorError) as exc:
        _raise_for(exc)
    return SwitchActiveResponse(device_id=device_id, active_agent_id=new_active)


@router.delete(
    "/{device_id}/agents/{agent_id}",
    response_model=DeleteAgentResponse,
)
async def delete_agent(
    device_id: str, agent_id: str, request: Request
) -> DeleteAgentResponse:
    orch = _orchestrator(request)
    try:
        new_active, fallback_kind = await orch.delete_agent(device_id, agent_id)
    except (DeviceNotFound, AgentNotFound, OrchestratorError) as exc:
        _raise_for(exc)
    return DeleteAgentResponse(
        device_id=device_id,
        deleted_agent_id=agent_id,
        new_active_agent_id=new_active,
        fallback_kind=fallback_kind,  # type: ignore[arg-type]
    )


# ---- soul (read / write) ---------------------------------------------------


@router.get(
    "/{device_id}/agents/{agent_id}/soul",
    response_model=SoulResponse,
)
async def get_soul(
    device_id: str, agent_id: str, request: Request
) -> SoulResponse:
    orch = _orchestrator(request)
    try:
        markdown, size_bytes = await orch.read_soul(device_id, agent_id)
    except (DeviceNotFound, AgentNotFound, OrchestratorError) as exc:
        _raise_for(exc)
    return SoulResponse(agent_id=agent_id, markdown=markdown, size_bytes=size_bytes)


@router.put(
    "/{device_id}/agents/{agent_id}/soul",
    response_model=UpdateSoulResponse,
)
async def update_soul(
    device_id: str,
    agent_id: str,
    body: UpdateSoulRequest,
    request: Request,
) -> UpdateSoulResponse:
    orch = _orchestrator(request)
    try:
        size_bytes = await orch.update_soul(device_id, agent_id, body.markdown)
    except (DeviceNotFound, AgentNotFound, SoulTooLarge, OrchestratorError) as exc:
        _raise_for(exc)
    return UpdateSoulResponse(agent_id=agent_id, size_bytes=size_bytes)
