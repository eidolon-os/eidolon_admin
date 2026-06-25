"""HTTP/SSE transparent proxy to upstream sub-projects."""
from __future__ import annotations

import os
import asyncio
from collections.abc import AsyncIterator, Iterable
from contextlib import suppress

import httpx
from eidolon_sdk.core.streaming import SSE_HEARTBEAT_BYTES
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
_SSE_HEARTBEAT_INTERVAL_SECONDS = 2.0


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


def _is_sse_response(resp: httpx.Response) -> bool:
    ctype = resp.headers.get("content-type", "").lower()
    return "text/event-stream" in ctype


async def _stream_raw(upstream_resp: httpx.Response) -> AsyncIterator[bytes]:
    try:
        async for chunk in upstream_resp.aiter_raw():
            yield chunk
    finally:
        await upstream_resp.aclose()


async def _stream_sse_with_heartbeat(upstream_resp: httpx.Response) -> AsyncIterator[bytes]:
    queue: asyncio.Queue[bytes | Exception | None] = asyncio.Queue()

    async def _read_upstream() -> None:
        try:
            async for chunk in upstream_resp.aiter_raw():
                await queue.put(chunk)
        except Exception as exc:
            await queue.put(exc)
        finally:
            await queue.put(None)

    reader = asyncio.create_task(_read_upstream())
    try:
        while True:
            try:
                item = await asyncio.wait_for(
                    queue.get(),
                    timeout=_SSE_HEARTBEAT_INTERVAL_SECONDS,
                )
            except TimeoutError:
                yield SSE_HEARTBEAT_BYTES
                continue

            if item is None:
                return
            if isinstance(item, Exception):
                raise item
            yield item
    finally:
        if not reader.done():
            reader.cancel()
            with suppress(asyncio.CancelledError):
                await reader
        await upstream_resp.aclose()


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
        stream_iter = (
            _stream_sse_with_heartbeat(upstream_resp)
            if _is_sse_response(upstream_resp)
            else _stream_raw(upstream_resp)
        )

        return StreamingResponse(
            stream_iter,
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
