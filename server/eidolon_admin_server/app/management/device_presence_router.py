"""Device-domain-scoped channel observations, independent of the runtime dashboard."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request

from eidolon_admin_server.app.gateway.proxy import upstream_auth_headers
from eidolon_admin_server.app.service_auth import require_local_api_credential

router = APIRouter(
    prefix="/internal/v1/management",
    tags=["management-internal"],
    dependencies=[Depends(require_local_api_credential)],
)


@router.get("/device-presence")
async def device_presence(request: Request, owner_domain_id: str) -> dict:
    """The Local API supplies a domain from its authorized device inventory."""
    service = request.app.state.registry.get("channel-provider")
    if service is None or not service.base_url:
        raise HTTPException(503, "设备连接观测服务未配置")
    url = (f"{service.base_url.rstrip('/')}{(service.upstream_prefix or '').rstrip('/')}"
           "/v1/device-channels/presence")
    try:
        response = await request.app.state.http_client.get(
            url, headers=upstream_auth_headers(service), timeout=5.0,
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("bodies") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise ValueError("invalid presence response")
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(503, "设备连接观测暂时不可用") from exc
    return {"bodies": [row for row in rows
                       if isinstance(row, dict)
                       and isinstance(row.get("device_ref"), dict)
                       and row["device_ref"].get("owner_domain_id") == owner_domain_id]}
