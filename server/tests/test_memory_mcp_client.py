"""Tests for ``app/memory/mcp_client.py`` error-handling contract.

The contract under test:
- ``probe_reachable(user_id)`` MUST NEVER raise. Returns False for any
  failure mode (user not configured, port not listening, server returns
  HTTP 502, server returns bad MCP framing, anything).
- ``open_session`` normalizes ALL non-HTTPException failures into
  :class:`MemoryAgentUnreachable` so callers see a clean 502 with a
  useful message — not a 500 / unhandled BaseExceptionGroup.

Real-call: we spin up a tiny HTTP server on a random port that returns
HTTP 502 to every request, then point a fake user entry at it. This
exercises the full ``streamablehttp_client`` → ``ClientSession`` → MCP
init handshake path that wedges in production.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest
from aiohttp import web

from eidolon_admin_server.app.memory import mcp_client as mc
from eidolon_admin_server.app.memory.mcp_client import (
    MemoryAgentUnreachable,
    MemoryUserNotFound,
    open_session,
    probe_reachable,
)
from eidolon_admin_server.app.memory.runners import UserEntry


# ---- helpers ----------------------------------------------------------------


@asynccontextmanager
async def _fake_mcp_returning_502(port: int):
    """Spin up an aiohttp server on ``port`` that returns 502 on every POST.

    This is the exact failure shape we saw in production: memory's FastMCP
    transport entered a wedged state and started returning 502 to every
    /mcp POST. The MCP client then raised a BaseExceptionGroup wrapping
    httpx.HTTPStatusError, which the OLD narrow ``except`` clause didn't
    catch and the FastAPI handler converted into a generic 500.
    """
    async def handler(_request: web.Request) -> web.Response:
        return web.Response(status=502, text="Bad Gateway")

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    try:
        yield
    finally:
        await runner.cleanup()


def _make_users(monkeypatch, *entries: UserEntry) -> None:
    """Patch load_users() so resolve_user finds the test entries."""
    monkeypatch.setattr(mc, "load_users", lambda: list(entries))


# ---- probe_reachable: never raises -----------------------------------------


async def test_probe_reachable_returns_false_for_unknown_user(monkeypatch) -> None:
    """Unknown user_id → MemoryUserNotFound inside, False outside."""
    _make_users(monkeypatch)  # no users
    assert await probe_reachable("nobody") is False


async def test_probe_reachable_returns_false_for_disabled_user(monkeypatch) -> None:
    _make_users(monkeypatch, UserEntry(id="alice", port=18801, enabled=False))
    assert await probe_reachable("alice") is False


async def test_probe_reachable_returns_false_when_port_closed(monkeypatch) -> None:
    """Port not listening → connection refused → False (not raise)."""
    _make_users(monkeypatch, UserEntry(id="alice", port=18802, enabled=True))
    assert await probe_reachable("alice") is False


async def test_probe_reachable_returns_false_when_upstream_returns_502(
    monkeypatch,
) -> None:
    """The exact production regression: MCP endpoint returns 502.

    Before the fix, this raised BaseExceptionGroup → 500 in FastAPI →
    broke /api/memory/users entirely. After the fix, probe returns
    False and the users-list endpoint stays up showing degraded state.
    """
    port = 18803
    _make_users(monkeypatch, UserEntry(id="alice", port=port, enabled=True))
    async with _fake_mcp_returning_502(port):
        result = await probe_reachable("alice")
    assert result is False


# ---- open_session: wraps everything as MemoryAgentUnreachable --------------


async def test_open_session_raises_user_not_found_unchanged(monkeypatch) -> None:
    """resolve_user errors must surface as their specific 4xx, not 502."""
    _make_users(monkeypatch)
    with pytest.raises(MemoryUserNotFound):
        async with open_session("nobody"):
            pass


async def test_open_session_wraps_connection_refused_as_unreachable(
    monkeypatch,
) -> None:
    _make_users(monkeypatch, UserEntry(id="alice", port=18804, enabled=True))
    with pytest.raises(MemoryAgentUnreachable) as exc_info:
        async with open_session("alice"):
            pass
    # The 502 envelope must reference the user + url so the operator can act.
    detail = exc_info.value.detail
    assert "'alice'" in detail
    assert ":18804/mcp" in detail


async def test_open_session_wraps_upstream_502_as_unreachable(monkeypatch) -> None:
    """The production-failure path: streamable_http surfaces HTTP 502.

    Verifies both: (a) we raise MemoryAgentUnreachable (HTTPException(502))
    so FastAPI returns a clean 502 to the caller, and (b) the underlying
    cause is preserved in the message ('502 Bad Gateway' from the inner
    httpx exception) rather than swallowed as 'TaskGroup error'.
    """
    port = 18805
    _make_users(monkeypatch, UserEntry(id="alice", port=port, enabled=True))
    async with _fake_mcp_returning_502(port):
        with pytest.raises(MemoryAgentUnreachable) as exc_info:
            async with open_session("alice"):
                pass
    detail = exc_info.value.detail
    assert exc_info.value.status_code == 502
    # The inner cause must surface — operator needs the actual symptom.
    assert "502" in detail or "Bad Gateway" in detail
