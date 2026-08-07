"""Tests for the unified gateway proxy."""

from __future__ import annotations

import asyncio

import httpx
import pytest
import respx

from eidolon_admin_server.app.gateway import proxy
from eidolon_admin_server.app.main import create_app
from eidolon_admin_server.app.settings import (
    AdminBindConfig,
    AuthConfig,
    GatewayConfig,
    ServiceConfig,
)


pytestmark = pytest.mark.asyncio


class _DelayedByteStream(httpx.AsyncByteStream):
    def __init__(self, *, delay: float, chunks: list[bytes]) -> None:
        self.delay = delay
        self.chunks = chunks

    async def __aiter__(self):
        await asyncio.sleep(self.delay)
        for chunk in self.chunks:
            yield chunk


@respx.mock
async def test_proxy_forwards_get(app):
    route = respx.get("http://agent.test/api/admin/personas/templates").mock(
        return_value=httpx.Response(200, json={"templates": ["a", "b"]})
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://gw") as ac:
        resp = await ac.get("/api/services/agent/personas/templates")

    assert resp.status_code == 200
    assert resp.json() == {"templates": ["a", "b"]}
    assert route.called


@respx.mock
async def test_proxy_unknown_service_returns_404(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://gw") as ac:
        resp = await ac.get("/api/services/nope/foo")
    assert resp.status_code == 404


@respx.mock
async def test_proxy_injects_bearer_token(app, monkeypatch):
    monkeypatch.setenv("TEST_MEMORY_TOKEN", "secret-xyz")
    captured: dict[str, str] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={"users": []})

    respx.get("http://memory.test/api/users").mock(side_effect=_capture)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://gw") as ac:
        # Send a frontend-provided Authorization that must be stripped
        resp = await ac.get(
            "/api/services/memory/users",
            headers={"Authorization": "Bearer client-token"},
        )

    assert resp.status_code == 200
    assert captured["auth"] == "Bearer secret-xyz"


@respx.mock
async def test_proxy_passes_operator_credential_only_for_declared_passthrough_service():
    captured: dict[str, str] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization", "")
        captured["cookie"] = request.headers.get("cookie", "")
        return httpx.Response(200, json={"operation": "device.directory-page"})

    respx.get("http://hub.test/api/device-management/v1/owners/owner-1/devices").mock(
        side_effect=_capture
    )
    config = GatewayConfig(
        admin=AdminBindConfig(cors_origins=[]),
        services=[
            ServiceConfig(
                id="hub",
                name="Hub",
                base_url="http://hub.test",
                auth=AuthConfig(type="passthrough"),
            )
        ],
    )
    subject = create_app(config)
    try:
        transport = httpx.ASGITransport(app=subject)
        async with httpx.AsyncClient(transport=transport, base_url="http://gw") as ac:
            response = await ac.get(
                "/api/services/hub/api/device-management/v1/owners/owner-1/devices",
                headers={
                    "Authorization": "Bearer operator",
                    "Cookie": "session=must-not-forward",
                },
            )
    finally:
        await subject.state.http_client.aclose()

    assert response.status_code == 200
    assert captured == {"auth": "Bearer operator", "cookie": ""}


@respx.mock
async def test_proxy_passes_query_string(app):
    route = respx.get("http://memory.test/api/memories?q=hello&top_k=5").mock(
        return_value=httpx.Response(200, json={"hits": []})
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://gw") as ac:
        resp = await ac.get("/api/services/memory/memories?q=hello&top_k=5")
    assert resp.status_code == 200
    assert route.called


@respx.mock
async def test_proxy_upstream_error_returns_502(app):
    respx.get("http://agent.test/api/admin/personas/templates").mock(
        side_effect=httpx.ConnectError("upstream down")
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://gw") as ac:
        resp = await ac.get("/api/services/agent/personas/templates")
    assert resp.status_code == 502
    body = resp.json()
    assert body["service_id"] == "agent"
    assert "upstream_error" in body


@respx.mock
async def test_proxy_post_body(app):
    captured = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content
        captured["ctype"] = request.headers.get("content-type")
        return httpx.Response(201, json={"ok": True})

    respx.post("http://agent.test/api/admin/personas/instances").mock(
        side_effect=_capture
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://gw") as ac:
        resp = await ac.post(
            "/api/services/agent/personas/instances",
            json={"template_id": "t1"},
        )

    assert resp.status_code == 201
    assert b"template_id" in captured["body"]
    assert "application/json" in captured["ctype"]


async def test_sse_proxy_emits_heartbeat_while_upstream_is_idle(monkeypatch):
    monkeypatch.setattr(proxy, "_SSE_HEARTBEAT_INTERVAL_SECONDS", 0.01)
    upstream = httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        stream=_DelayedByteStream(delay=0.03, chunks=[b"data: done\n\n"]),
    )

    chunks = []
    async for chunk in proxy._stream_sse_with_heartbeat(upstream):
        chunks.append(chunk)

    assert b": keepalive\n\n" in chunks
    assert chunks[-1] == b"data: done\n\n"


async def test_services_catalog(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://gw") as ac:
        resp = await ac.get("/api/services")
    assert resp.status_code == 200
    data = resp.json()
    ids = [s["id"] for s in data["services"]]
    assert ids == ["agent", "memory"]
    # token_env / script must NOT leak
    assert "token_env" not in data["services"][1].get("auth", {})


@respx.mock
async def test_overview_probes_concurrently(app):
    """Overview endpoint runs HTTP probes against every service in parallel.

    Comprehensive overview tests live in test_overview.py — this one just
    confirms the http_client + registry wiring works from the proxy fixture.
    """
    respx.get("http://agent.test/api/admin/personas/templates").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.get("http://memory.test/api/health").mock(
        return_value=httpx.Response(503, json={"status": "down"})
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://gw") as ac:
        resp = await ac.get("/api/overview/services")
    assert resp.status_code == 200
    data = resp.json()
    by_id = {s["id"]: s for s in data["services"]}
    # agent returned 200, memory returned 503 → http_probe.ok flips accordingly
    assert by_id["agent"]["http_probe"]["ok"] is True
    assert by_id["memory"]["http_probe"]["ok"] is False
