"""Service catalog endpoint — feeds the frontend menu."""
from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(prefix="/services", tags=["catalog"])


@router.get("")
async def list_services(request: Request) -> dict:
    registry = request.app.state.registry
    items = []
    for svc in registry.services:
        supervisor_meta = None
        if svc.supervisor is not None:
            supervisor_meta = {
                "group": svc.supervisor.group,
                "programs": list(svc.supervisor.programs),
            }
        items.append(
            {
                "id": svc.id,
                "name": svc.name,
                "integration": svc.integration,
                "features": [f.model_dump() for f in svc.features],
                "supervisor": supervisor_meta,
                "auth_type": svc.auth.type,
            }
        )
    return {"services": items}
