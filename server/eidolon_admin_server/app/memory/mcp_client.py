"""Per-request MCP HTTP session manager.

Each call opens a fresh Streamable-HTTP MCP session against the user's
agent_runner port (looked up in users.yaml), invokes a tool, then closes.
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

from fastapi import HTTPException
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from .runners import UserEntry, load_users, users_yaml_path

logger = logging.getLogger(__name__)


# How memory's agent_runner exposes MCP. Mirrors McpHttpConfig defaults.
_MCP_HOST = "127.0.0.1"
_MCP_PATH = "/mcp"
_DEFAULT_CONNECT_ATTEMPTS = 4
_DEFAULT_BACKOFF_SECONDS = 0.4


class MemoryUserNotFound(HTTPException):
    def __init__(self, user_id: str) -> None:
        super().__init__(404, f"user not found in users.yaml: {user_id!r}")


class MemoryUserDisabled(HTTPException):
    def __init__(self, user_id: str) -> None:
        super().__init__(403, f"user is disabled: {user_id!r}")


class MemoryAgentUnreachable(HTTPException):
    def __init__(self, user_id: str, url: str, inner: Exception) -> None:
        super().__init__(
            502,
            f"agent_runner for user {user_id!r} unreachable at {url}: {inner}",
        )


def mcp_url_for_port(port: int) -> str:
    return f"http://{_MCP_HOST}:{port}{_MCP_PATH}"


def resolve_user(user_id: str) -> UserEntry:
    """Return the UserEntry for ``user_id`` or raise HTTP-friendly errors."""
    users = load_users()
    for u in users:
        if u.id == user_id:
            if not u.enabled:
                raise MemoryUserDisabled(user_id)
            return u
    raise MemoryUserNotFound(user_id)


def _bearer_token() -> str | None:
    token = os.environ.get("EIDOLON_MEMORY_MCP_TOKEN", "").strip()
    return token or None


@asynccontextmanager
async def open_session(user_id: str) -> AsyncIterator[tuple[ClientSession, str]]:
    """Open a fresh MCP session for ``user_id``. Yields (session, url).

    Caller is responsible for awaiting tool calls inside the ``async with``.
    Errors connecting raise :class:`MemoryAgentUnreachable` (HTTP 502).
    """
    user = resolve_user(user_id)
    url = mcp_url_for_port(user.port)
    headers: dict[str, str] = {}
    token = _bearer_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        async with streamablehttp_client(url, headers=headers or None) as (
            read,
            write,
            _get_session_id,
        ):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session, url
    except (ConnectionError, OSError, RuntimeError) as exc:
        raise MemoryAgentUnreachable(user_id, url, exc) from exc


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
    user_id: str,
    tool: str,
    arguments: dict[str, Any] | None = None,
) -> Any:
    """One-shot MCP tool call. Returns decoded JSON or raises HTTPException."""
    async with open_session(user_id) as (session, _url):
        result = await session.call_tool(tool, arguments or {})
        if result.isError:
            err = _parse_text_content(result.content) or "tool returned error"
            raise HTTPException(502, f"tool {tool!r} error: {err}")
        return _parse_text_content(result.content)


async def list_tools(user_id: str) -> list[dict[str, Any]]:
    """List MCP tools exposed by the agent_runner."""
    async with open_session(user_id) as (session, _url):
        result = await session.list_tools()
        return [
            {
                "name": t.name,
                "description": t.description or "",
                "input_schema": t.inputSchema or {},
            }
            for t in result.tools
        ]


async def probe_reachable(user_id: str) -> bool:
    """Cheap liveness probe — opens a session and immediately closes."""
    try:
        async with open_session(user_id):
            return True
    except HTTPException:
        return False
