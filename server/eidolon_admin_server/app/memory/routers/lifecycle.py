"""Legacy memory lifecycle endpoints.

User registry writes moved to ``/api/users``. This module keeps a few memory
runtime helpers, but create/enable/consolidator writes are rejected so memory
cannot maintain a second enabled flag.
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status

from ..mcp_client import mcp_url_for_port, probe_reachable
from ..runners import UserEntry, load_users
from ..presentation import agent_log_path_for, consolidator_status, memory_runtime_state
from ..runners import (
    find_agent_processes,
    find_consolidator_processes,
)
from ..schemas import (
    ConsolidatorUpdateRequest,
    UserCreateRequest,
    UserDetail,
    UserMutateResponse,
)
from ..supervisor_hooks import sighup_memory_supervisor, wait_for_user_reachable

logger = logging.getLogger(__name__)

router = APIRouter()


def _get_user(user_id: str) -> UserEntry | None:
    return next((u for u in load_users() if u.id == user_id), None)


def _detail(entry: UserEntry, *, reachable: bool = False) -> UserDetail:
    agent_map = find_agent_processes()
    cons_map = find_consolidator_processes()
    proc = agent_map.get(entry.id)
    worker_running = proc is not None
    palace_initialized = reachable
    return UserDetail(
        user_id=entry.id,
        port=entry.port,
        enabled=entry.enabled,
        palace_path=entry.palace_path,
        mcp_http_url=mcp_url_for_port(entry.port),
        agent_reachable=reachable,
        palace_initialized=palace_initialized,
        worker_running=worker_running,
        runtime_state=memory_runtime_state(
            entry,
            worker_running=worker_running,
            agent_reachable=reachable,
            palace_initialized=palace_initialized,
        ),
        pid=proc.pid if proc else None,
        agent_log_path=agent_log_path_for(entry),
        log_path=agent_log_path_for(entry),
        consolidator=consolidator_status(entry, cons_map=cons_map),
    )


async def _reconcile(
    request: Request,
    user_id: str,
    *,
    wait_for_reachable: bool,
) -> dict:
    """Trigger SIGHUP and (if requested) wait for the agent to answer.

    Returns a small dict describing what happened, used in the API response.
    """
    sv_client = request.app.state.supervisor_client
    sighup_result = await sighup_memory_supervisor(sv_client)
    reachable = False
    if wait_for_reachable and sighup_result.get("signaled"):
        reachable = await wait_for_user_reachable(user_id, timeout_seconds=10.0)
    return {"reconcile": sighup_result, "agent_reachable": reachable}


# -- create -------------------------------------------------------------------


@router.post(
    "/users",
    response_model=UserMutateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_user(body: UserCreateRequest, request: Request) -> UserMutateResponse:
    del body, request
    raise HTTPException(
        status_code=409,
        detail="memory no longer owns users; create users through /api/users",
    )


# -- enable / disable ---------------------------------------------------------


@router.post("/users/{user_id}/enable", response_model=UserMutateResponse)
async def enable_user(
    user_id: str, request: Request, enabled: bool = True
) -> UserMutateResponse:
    del request, enabled
    raise HTTPException(
        status_code=409,
        detail=(
            f"memory no longer owns enabled for {user_id!r}; "
            f"use /api/users/{user_id}/enable"
        ),
    )


# -- start / stop (sugar over enable=true/false) ------------------------------


@router.post("/users/{user_id}/start", response_model=UserMutateResponse)
async def start_user(user_id: str, request: Request) -> UserMutateResponse:
    return await enable_user(user_id, request, enabled=True)


@router.post("/users/{user_id}/stop", response_model=UserMutateResponse)
async def stop_user(user_id: str, request: Request) -> UserMutateResponse:
    return await enable_user(user_id, request, enabled=False)


# -- consolidator -------------------------------------------------------------


@router.put("/users/{user_id}/consolidator", response_model=UserMutateResponse)
async def update_user_consolidator(
    user_id: str,
    body: ConsolidatorUpdateRequest,
    request: Request,
) -> UserMutateResponse:
    del body, request
    raise HTTPException(
        status_code=409,
        detail=(
            f"memory no longer owns consolidator config for {user_id!r}; "
            f"use /api/users/{user_id}"
        ),
    )


@router.delete("/users/{user_id}/consolidator", response_model=UserMutateResponse)
async def remove_user_consolidator(user_id: str, request: Request) -> UserMutateResponse:
    del request
    raise HTTPException(
        status_code=409,
        detail=(
            f"memory no longer owns consolidator config for {user_id!r}; "
            f"use /api/users/{user_id}"
        ),
    )


# -- init palace --------------------------------------------------------------


@router.post("/users/{user_id}/init", response_model=UserMutateResponse)
async def init_user_palace(user_id: str, request: Request) -> UserMutateResponse:
    """Create the palace directory shell, then let the agent_runner lazy-init
    its store on next startup.

    The agent_runner does all the heavy lifting (chromadb, KG sqlite, etc.) on
    first MCP call — we just guarantee the directory exists with the right
    ownership.
    """
    entry = _get_user(user_id)
    if entry is None:
        raise HTTPException(404, f"unknown user: {user_id!r}")

    palace_dir = Path(entry.palace_path).expanduser() if entry.palace_path else None
    if palace_dir:
        try:
            palace_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise HTTPException(500, f"mkdir {palace_dir} failed: {exc}") from exc

    # If the user is currently disabled, we don't poll for MCP — caller can
    # call /start afterwards.
    info = await _reconcile(request, user_id, wait_for_reachable=entry.enabled)
    return UserMutateResponse(
        user=_detail(entry, reachable=info["agent_reachable"]),
        message=(
            f"palace ensured for user '{user_id}' at "
            f"{palace_dir or '(default)'}; reconcile={info['reconcile']}"
        ),
    )
