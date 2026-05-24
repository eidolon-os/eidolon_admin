"""HTTP/SSE transparent proxy to upstream sub-projects."""
from __future__ import annotations

import os
from typing import Iterable

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from ..settings import ServiceConfig

# Headers we drop from the incoming request before forwarding.
_HOP_BY_HOP = {
    "host",
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "content-length",
}
# Headers from frontend we never forward (admin gateway owns auth).
_REQUEST_DROP = _HOP_BY_HOP | {"authorization", "cookie"}
# Headers we drop from upstream response before returning.
_RESPONSE_DROP = _HOP_BY_HOP | {"content-encoding"}


def _filter_request_headers(headers: Iterable[tuple[str, str]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in headers:
        if k.lower() in _REQUEST_DROP:
            continue
        out[k] = v
    return out


def _filter_response_headers(headers: httpx.Headers) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() not in _RESPONSE_DROP}


def _inject_upstream_auth(service: ServiceConfig, headers: dict[str, str]) -> None:
    if service.auth.type == "bearer":
        env = service.auth.token_env
        token = os.environ.get(env) if env else None
        if token:
            headers["Authorization"] = f"Bearer {token}"


def _build_target_url(service: ServiceConfig, sub_path: str, query_string: str) -> str:
    sub = sub_path.lstrip("/")
    prefix = service.upstream_prefix.rstrip("/")
    url = f"{service.base_url}{prefix}/{sub}" if sub else f"{service.base_url}{prefix}"
    if query_string:
        url = f"{url}?{query_string}"
    return url


def _is_stream_response(resp: httpx.Response) -> bool:
    ctype = resp.headers.get("content-type", "").lower()
    return "text/event-stream" in ctype or "application/x-ndjson" in ctype


async def proxy_request(
    request: Request,
    service: ServiceConfig,
    sub_path: str,
    client: httpx.AsyncClient,
) -> Response:
    url = _build_target_url(service, sub_path, request.url.query)
    body = await request.body()
    headers = _filter_request_headers(request.headers.items())
    _inject_upstream_auth(service, headers)

    upstream_req = client.build_request(
        method=request.method,
        url=url,
        headers=headers,
        content=body or None,
    )
    upstream_resp = await client.send(upstream_req, stream=True)

    if _is_stream_response(upstream_resp):
        async def _aiter():
            try:
                async for chunk in upstream_resp.aiter_raw():
                    yield chunk
            finally:
                await upstream_resp.aclose()

        return StreamingResponse(
            _aiter(),
            status_code=upstream_resp.status_code,
            headers=_filter_response_headers(upstream_resp.headers),
            media_type=upstream_resp.headers.get("content-type"),
        )

    try:
        content = await upstream_resp.aread()
    finally:
        await upstream_resp.aclose()

    return Response(
        content=content,
        status_code=upstream_resp.status_code,
        headers=_filter_response_headers(upstream_resp.headers),
        media_type=upstream_resp.headers.get("content-type"),
    )


def upstream_error_response(exc: Exception, service_id: str) -> JSONResponse:
    return JSONResponse(
        status_code=502,
        content={
            "upstream_error": str(exc),
            "service_id": service_id,
        },
    )
