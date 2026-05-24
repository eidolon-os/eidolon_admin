"""Unified proxy router — mounts ANY /api/services/{service_id}/{sub_path:path}."""
from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException, Request

from .proxy import proxy_request, upstream_error_response
from .registry import ServiceRegistry

router = APIRouter()


def _get_registry(request: Request) -> ServiceRegistry:
    return request.app.state.registry


def _get_client(request: Request) -> httpx.AsyncClient:
    return request.app.state.http_client


@router.api_route(
    "/services/{service_id}/{sub_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
)
async def proxy(service_id: str, sub_path: str, request: Request):
    registry = _get_registry(request)
    service = registry.get(service_id)
    if service is None:
        raise HTTPException(status_code=404, detail=f"unknown service: {service_id}")
    if not service.base_url:
        raise HTTPException(
            status_code=404,
            detail=(
                f"service {service_id!r} does not proxy to an upstream — "
                "use the native /api endpoints under this gateway instead"
            ),
        )
    try:
        return await proxy_request(request, service, sub_path, _get_client(request))
    except (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError) as exc:
        return upstream_error_response(exc, service_id)
