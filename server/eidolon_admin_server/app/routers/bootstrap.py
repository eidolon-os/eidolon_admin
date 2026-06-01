"""Bootstrap-state aggregator — Phase 29.J.

A pure read-only endpoint the frontend polls on startup to decide
whether to show the first-run onboarding banner and, if so, which step
the operator is on.

Why a separate router instead of folding into ``/api/overview``:
overview is a generic admin landing endpoint that also serves the
service catalog; this one is specifically about Phase 29's five-entity
flow. Keeping it isolated means we can tighten its contract (and tests)
without touching overview.

The endpoint never errors — if an orchestrator is missing or its
upstream is down we report that step as "unknown" rather than failing
the whole probe. The frontend's banner is purely advisory; we don't
want a transient memory hiccup to turn it into a misleading scare.
"""
from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from ..registry.agents.orchestrator import AgentOrchestrator
from ..registry.devices.orchestrator import DeviceOrchestrator
from ..registry.templates.orchestrator import TemplateOrchestrator
from ..registry.tenants.orchestrator import TenantOrchestrator
from ..registry.users.orchestrator import UserOrchestrator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/bootstrap", tags=["bootstrap"])


StepStatus = Literal["ok", "empty", "unknown"]


class BootstrapStep(BaseModel):
    """One entity's bootstrap state.

    - ``ok``: at least one row exists, this step is done.
    - ``empty``: the count is zero, operator needs to act on this step.
    - ``unknown``: upstream service was down so we can't tell. Banner
      stays hidden for unknown steps (don't pester operators with
      noisy onboarding when the system is just rebooting).
    """

    status: StepStatus
    count: int = Field(ge=0, default=0)


class BootstrapState(BaseModel):
    """Frontend's onboarding state machine.

    ``ready`` = every required step is ``ok``. ``next_step`` points at
    the first non-ok step so the banner can jump directly to it.
    """

    tenants: BootstrapStep
    templates: BootstrapStep
    users: BootstrapStep
    agents: BootstrapStep
    devices: BootstrapStep
    ready: bool
    next_step: str | None = None


async def _safe_count(label: str, fn) -> BootstrapStep:
    """Run an async list/count callable; map exceptions to ``unknown``."""
    try:
        n = await fn()
        return BootstrapStep(status="ok" if n > 0 else "empty", count=n)
    except Exception:  # noqa: BLE001 — bootstrap probe must never raise
        logger.warning("bootstrap probe failed for %s", label, exc_info=True)
        return BootstrapStep(status="unknown", count=0)


def _orch(request: Request, attr: str):
    return getattr(request.app.state, attr, None)


@router.get("/state", response_model=BootstrapState)
async def get_state(request: Request) -> BootstrapState:
    """Per-entity counts + a hint at the first incomplete step.

    Probing each orchestrator individually rather than via the
    public list endpoints so we don't pay HTTP overhead for the
    common "everything's fine" case. If an orchestrator is None
    (lifespan couldn't wire it), the step reports ``unknown``.
    """
    tenant_orch: TenantOrchestrator | None = _orch(request, "tenant_orchestrator")
    template_orch: TemplateOrchestrator | None = _orch(request, "template_orchestrator")
    user_orch: UserOrchestrator | None = _orch(request, "user_orchestrator")
    agent_orch: AgentOrchestrator | None = _orch(request, "agent_orchestrator")
    device_orch: DeviceOrchestrator | None = _orch(request, "device_orchestrator")

    async def _from_orch(orch, list_call: str) -> int:
        rows = await getattr(orch, list_call)()
        return len(rows)

    tenants = (
        await _safe_count("tenants", lambda: _from_orch(tenant_orch, "list_all"))
        if tenant_orch
        else BootstrapStep(status="unknown")
    )
    templates = (
        await _safe_count("templates", lambda: _from_orch(template_orch, "list_all"))
        if template_orch
        else BootstrapStep(status="unknown")
    )
    users = (
        await _safe_count("users", lambda: _from_orch(user_orch, "list_users"))
        if user_orch
        else BootstrapStep(status="unknown")
    )
    agents = (
        await _safe_count("agents", lambda: _from_orch(agent_orch, "list_agents"))
        if agent_orch
        else BootstrapStep(status="unknown")
    )
    devices = (
        await _safe_count("devices", lambda: _from_orch(device_orch, "list_devices"))
        if device_orch
        else BootstrapStep(status="unknown")
    )

    # Order matches the dependency chain operators must follow:
    # tenant exists → pick template → create user → create agent →
    # bind device. The first "empty" wins; "unknown" steps don't
    # block because we can't be sure they're really empty.
    ordered: list[tuple[str, BootstrapStep]] = [
        ("tenants", tenants),
        ("templates", templates),
        ("users", users),
        ("agents", agents),
        ("devices", devices),
    ]
    next_step: str | None = None
    for name, step in ordered:
        if step.status == "empty":
            next_step = name
            break

    ready = all(s.status == "ok" for _, s in ordered)

    return BootstrapState(
        tenants=tenants,
        templates=templates,
        users=users,
        agents=agents,
        devices=devices,
        ready=ready,
        next_step=next_step,
    )
