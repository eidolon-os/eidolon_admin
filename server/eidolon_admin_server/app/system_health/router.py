"""REST surface for system health auditing.

Two endpoints:
  - ``GET  /api/system/health``: full audit (port state per service +
    orphan list). Read-only, no side effects.
  - ``POST /api/system/orphans/kill``: operator-confirmed orphan kill.
    Body carries pid + port so a state change between view and click
    refuses the kill instead of risking a wrong-pid action.

The router itself does no logic — input validation via Pydantic, then
one call into the auditor. Same pattern as the devices module.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from .auditor import SystemHealthAuditor
from .schemas import (
    KillOrphanRequest,
    KillOrphanResponse,
    OrphanProcess,
    PortStatus,
    ServiceHealth,
    SystemHealthResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/system", tags=["system"])


def _auditor(request: Request) -> SystemHealthAuditor:
    """Build a fresh auditor per request.

    Auditor itself is stateless across calls — constructing one is
    just stashing two references. Doing it per-request keeps the
    config snapshot fresh (cfg comes from app.state.gateway_config
    which may have been reloaded since last audit).
    """
    cfg = request.app.state.gateway_config
    sv = request.app.state.supervisor_client
    return SystemHealthAuditor(cfg, sv)


@router.get("/health", response_model=SystemHealthResponse)
async def system_health(request: Request) -> SystemHealthResponse:
    audit = await _auditor(request).audit()

    services_payload: list[ServiceHealth] = []
    for s in audit.services:
        services_payload.append(ServiceHealth(
            service_id=s.service_id,
            service_name=s.service_name,
            supervised=s.supervised,
            supervisor_pids=list(s.supervisor_pids),
            ports=[
                PortStatus(
                    port=p.port,
                    state=p.state,  # type: ignore[arg-type]
                    listener_pid=p.listener.pid if p.listener else None,
                    listener_command=p.listener.command if p.listener else None,
                    listener_ppid=p.listener.ppid if p.listener else None,
                    listener_ppid_chain=p.listener_ppid_chain,
                    supervised=p.supervised,
                )
                for p in s.ports
            ],
        ))

    return SystemHealthResponse(
        supervisord_reachable=audit.supervisord_reachable,
        supervisord_pid=audit.supervisord_pid,
        services=services_payload,
        orphans=[
            OrphanProcess(
                pid=o.pid,
                ppid=o.ppid,
                command=o.command,
                declared_for_service=o.declared_for_service,
                port=o.port,
                age_seconds=o.age_seconds,
            )
            for o in audit.orphans
        ],
    )


@router.post("/orphans/kill", response_model=KillOrphanResponse)
async def kill_orphan(
    body: KillOrphanRequest, request: Request,
) -> KillOrphanResponse:
    """SIGTERM an orphan process the operator explicitly identified.

    Returns 200 with ``signaled=False`` + error message rather than 4xx
    when the kill fails — UI can show the message inline. 4xx is
    reserved for input shape errors.
    """
    ok, err = await _auditor(request).kill_orphan(
        pid=body.pid, expected_port=body.port,
    )
    if not ok and err and "access denied" in err.lower():
        # This is the one case where 403 communicates the situation
        # better than a 200-with-error: the operator literally cannot
        # do this without privilege escalation outside admin.
        raise HTTPException(status_code=403, detail=err)
    return KillOrphanResponse(pid=body.pid, signaled=ok, error=err)
