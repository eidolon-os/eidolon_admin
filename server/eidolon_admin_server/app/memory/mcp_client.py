"""Per-request MCP HTTP session manager.

Each call opens a fresh Streamable-HTTP MCP session against the memory realm's
agent_runner port, invokes a tool, then closes.
Matches eidolon_memory's mcp_client.py:59-89 pattern — no caching, no
long-lived sessions, intentionally simple.

Why per-request:
- agent_runner restarts frequently in dev; cached sessions go stale
- MCP HTTP sessions are cheap to open (~10-25 ms)
- The admin UI is low-frequency: humans, not loops
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import httpx
from fastapi import HTTPException
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from .runners import RealmEntry, load_realms

logger = logging.getLogger(__name__)


class MemoryRealmNotFound(HTTPException):
    def __init__(self, memory_realm_id: str) -> None:
        super().__init__(404, f"memory realm not found: {memory_realm_id!r}")


class MemoryRealmDisabled(HTTPException):
    def __init__(self, memory_realm_id: str) -> None:
        super().__init__(403, f"memory realm is disabled: {memory_realm_id!r}")


class MemoryAgentUnreachable(HTTPException):
    def __init__(self, memory_realm_id: str, url: str, inner: Exception) -> None:
        super().__init__(
            502,
            f"agent_runner for memory realm {memory_realm_id!r} unreachable at {url}: {inner}",
        )


def resolve_realm(memory_realm_id: str) -> RealmEntry:
    """Return the RealmEntry for ``memory_realm_id`` or raise HTTP-friendly errors."""
    realms = load_realms()
    for u in realms:
        if u.memory_realm_id == memory_realm_id:
            if not u.enabled:
                raise MemoryRealmDisabled(memory_realm_id)
            return u
    raise MemoryRealmNotFound(memory_realm_id)


def _bearer_token() -> str | None:
    token = os.environ.get("EIDOLON_MEMORY_MCP_TOKEN", "").strip()
    return token or None


def _local_http_client(
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout | None = None,
    auth: httpx.Auth | None = None,
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers=headers,
        timeout=timeout,
        auth=auth,
        follow_redirects=True,
        trust_env=False,
    )


@asynccontextmanager
async def open_session(
    memory_realm_id: str,
) -> AsyncIterator[tuple[ClientSession, str]]:
    """Open a fresh MCP session for ``memory_realm_id``. Yields (session, url).

    Caller is responsible for awaiting tool calls inside the ``async with``.
    Any failure connecting / initializing the MCP session is normalized
    into :class:`MemoryAgentUnreachable` (HTTP 502) so callers see a single
    well-typed error instead of a zoo of httpx/anyio/runtime exceptions.

    Why such a broad ``except``:
        ``streamablehttp_client`` wraps its internals in an
        ``anyio.TaskGroup``, so any failure inside (HTTP 502 from the
        server, a flaky session resume, etc.) arrives as a
        ``BaseExceptionGroup`` — NOT one of ``ConnectionError /
        OSError / RuntimeError``. The previous narrow except let those
        groups propagate up and the FastAPI handler raised a 500. That
        masked the real symptom ("agent unreachable") under a generic
        crash and broke every /api/memory/* endpoint as soon as the
        user-worker entered a degraded MCP state. We'd rather always
        report 502 with the underlying message — same contract whether
        the failure is connect-refused, 502 from server, or a stream
        protocol violation.
    """
    realm = resolve_realm(memory_realm_id)
    url = realm.mcp_http_url
    headers: dict[str, str] = {}
    token = _bearer_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        async with streamablehttp_client(
            url,
            headers=headers or None,
            httpx_client_factory=_local_http_client,
        ) as (
            read,
            write,
            _get_session_id,
        ):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session, url
    except HTTPException:
        # MemoryRealmNotFound / MemoryRealmDisabled are HTTPException subclasses
        # raised by resolve_realm(); re-raise unchanged so the user sees
        # the precise 404/403 rather than a misleading 502.
        raise
    except BaseException as exc:  # noqa: BLE001 — see docstring
        # Drill down into BaseExceptionGroup to surface a useful message
        # (the inner cause, not the wrapper).
        inner: BaseException = exc
        while isinstance(inner, BaseExceptionGroup) and inner.exceptions:
            inner = inner.exceptions[0]
        # Cast to Exception for the constructor (we know it's catchable).
        if not isinstance(inner, Exception):
            inner = RuntimeError(str(inner) or type(inner).__name__)
        raise MemoryAgentUnreachable(memory_realm_id, url, inner) from exc


def _unwrap(payload: Any) -> Any:
    """fastmcp wraps tool returns in {'result': ...} when input_schema set."""
    if isinstance(payload, dict) and set(payload) == {"result"}:
        return payload["result"]
    return payload


def _parse_text_content(content: list[Any]) -> Any:
    """MCP tool returns a list of content items; we expect a single text JSON."""
    for item in content:
        text = getattr(item, "text", None)
        if text is None and isinstance(item, dict):
            text = item.get("text")
        if not isinstance(text, str):
            continue
        try:
            return _unwrap(json.loads(text))
        except json.JSONDecodeError:
            return text
    return None


async def call_tool(
    memory_realm_id: str,
    tool: str,
    arguments: dict[str, Any] | None = None,
) -> Any:
    """One-shot MCP tool call. Returns decoded JSON or raises HTTPException."""
    async with open_session(memory_realm_id) as (session, _url):
        result = await session.call_tool(tool, arguments or {})
        if result.isError:
            err = _parse_text_content(result.content) or "tool returned error"
            raise HTTPException(502, f"tool {tool!r} error: {err}")
        return _parse_text_content(result.content)


async def list_tools(memory_realm_id: str) -> list[dict[str, Any]]:
    """List MCP tools exposed by the agent_runner."""
    async with open_session(memory_realm_id) as (session, _url):
        result = await session.list_tools()
        return [
            {
                "name": t.name,
                "description": t.description or "",
                "input_schema": t.inputSchema or {},
            }
            for t in result.tools
        ]


async def probe_reachable(memory_realm_id: str) -> bool:
    """Cheap liveness probe — opens a session and immediately closes.

    Returns True iff a fresh MCP session can be established and initialized
    against the realm's agent_runner.

    **This function must never raise** — it's called from /api/memory/realms
    in an ``asyncio.gather`` over every configured realm. A single realm with
    a wedged MCP transport must not crash the whole realms-list response.
    Any failure (HTTPException from resolve_realm, MemoryAgentUnreachable
    from connect, or anything genuinely unexpected) is logged and returned
    as False. The caller renders this as "unreachable" in the UI, which is
    exactly the operator-actionable signal we want.
    """
    try:
        async with open_session(memory_realm_id):
            return True
    except HTTPException:
        # Expected "not reachable" path — user disabled, agent down, etc.
        return False
    except BaseException as exc:  # noqa: BLE001 — see docstring
        logger.warning("probe_reachable(%s) unexpected error: %s", memory_realm_id, exc)
        return False
