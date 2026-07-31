"""Admin-side adapter for per-realm memory runtime routes."""

from __future__ import annotations

import os
from typing import Any

from eidolon_memory_contracts import (
    DEFAULT_MEMORY_MCP_BASE_PORT,
    DEFAULT_MEMORY_MCP_HOST,
    DEFAULT_MEMORY_MCP_PATH,
    MemoryRuntimeRoute,
    memory_runtime_route_for_realm,
)

from ..ports import load_ports


def default_mcp_base_port() -> int:
    raw = os.environ.get("EIDOLON_MEMORY_MCP_PORT", "").strip()
    if raw:
        try:
            port = int(raw)
        except ValueError:
            port = 0
        if 1 <= port <= 65535:
            return port
    try:
        ports = load_ports()
    except Exception:  # noqa: BLE001 - route fallback must not break admin pages
        ports = {}
    memory = ports.get("memory") if isinstance(ports, dict) else {}
    mcp = memory.get("mcp") if isinstance(memory, dict) else {}
    try:
        port = int(mcp.get("port", 0) or 0)
    except (TypeError, ValueError):
        port = 0
    return port if 1 <= port <= 65535 else DEFAULT_MEMORY_MCP_BASE_PORT


def route_for_realm(
    memory_realm_id: str,
    *,
    base_port: int | None = None,
    used_ports: set[int] | None = None,
) -> MemoryRuntimeRoute:
    return memory_runtime_route_for_realm(
        memory_realm_id,
        base_port=base_port or default_mcp_base_port(),
        used_ports=used_ports,
        mcp_host=DEFAULT_MEMORY_MCP_HOST,
        mcp_path=DEFAULT_MEMORY_MCP_PATH,
    )


def nats_subject_templates(memory_realm_subject_token: str) -> dict[str, Any]:
    """Expose the write-plane contract without embedding raw realm ids in subjects."""
    token = memory_realm_subject_token
    return {
        "turn": f"eidolon.memory.turn.{token}",
        "cmd": f"eidolon.memory.cmd.{token}",
        "sync": f"eidolon.memory.sync.{token}",
    }
