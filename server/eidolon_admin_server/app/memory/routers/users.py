"""Read endpoints for memory users.

GET /api/memory/users — list users.yaml entries augmented with live agent
reachability via per-request MCP probe.

Write endpoints (create/init/start/stop/enable) live in lifecycle.py (Phase 15).
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter

from ..mcp_client import call_tool, probe_reachable, mcp_url_for_port
from ..runners import UserEntry, load_users, users_yaml_path
from ..schemas import UserDetail, UsersListResponse

router = APIRouter()


async def _build_detail(entry: UserEntry) -> UserDetail:
    detail = UserDetail(
        user_id=entry.id,
        port=entry.port,
        enabled=entry.enabled,
        palace_path=entry.palace_path,
        mcp_http_url=mcp_url_for_port(entry.port),
    )
    if not entry.enabled:
        return detail
    detail.agent_reachable = await probe_reachable(entry.id)
    if detail.agent_reachable:
        try:
            status = await call_tool(entry.id, "eidolon_memory_status")
            if isinstance(status, dict):
                detail.runner_status = status
                detail.palace_initialized = bool(
                    status.get("palace_initialized")
                    or status.get("ready")
                )
        except Exception:  # noqa: BLE001 — best-effort enrichment
            pass
    return detail


@router.get("/users", response_model=UsersListResponse)
async def list_memory_users() -> UsersListResponse:
    entries = load_users()
    details = await asyncio.gather(*(_build_detail(e) for e in entries))
    return UsersListResponse(
        users_file=str(users_yaml_path()),
        users=details,
        default_user_id=entries[0].id if entries else "",
    )
