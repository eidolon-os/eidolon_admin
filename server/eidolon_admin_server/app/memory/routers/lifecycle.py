"""User lifecycle: create / enable / disable / init / start / stop.

All mutations go through the same recipe:

    1. atomically write users.yaml
    2. SIGHUP memory-supervisor (reconciles agent + consolidator children)
    3. (optionally) poll the MCP endpoint until the user's agent is reachable

Direct subprocess management is intentionally **NOT** used here — that's
memory-supervisor's job. We're just editing the source of truth and letting
the in-process supervisor do what it already knows how to do.
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status

from ..mcp_client import mcp_url_for_port, probe_reachable
from ..runners import UserEntry, load_users
from ..presentation import agent_log_path_for, consolidator_status
from ..runners import ConsolidatorConfig, find_consolidator_processes
from ..schemas import (
    ConsolidatorUpdateRequest,
    UserCreateRequest,
    UserDetail,
    UserMutateResponse,
)
from ..supervisor_hooks import sighup_memory_supervisor, wait_for_user_reachable
from ..users_yaml import UsersYamlError, get_user, set_consolidator, set_enabled, upsert_user

logger = logging.getLogger(__name__)

router = APIRouter()


def _detail(entry: UserEntry, *, reachable: bool = False) -> UserDetail:
    cons_map = find_consolidator_processes()
    return UserDetail(
        user_id=entry.id,
        port=entry.port,
        enabled=entry.enabled,
        palace_path=entry.palace_path,
        mcp_http_url=mcp_url_for_port(entry.port),
        agent_reachable=reachable,
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
    entry = UserEntry(
        id=body.id,
        port=body.port,
        enabled=body.enabled,
        palace_path=body.palace_path,
    )
    try:
        upsert_user(entry)
    except UsersYamlError as exc:
        raise HTTPException(409, str(exc)) from exc

    info = await _reconcile(request, body.id, wait_for_reachable=body.enabled)
    detail = _detail(entry, reachable=info["agent_reachable"])
    return UserMutateResponse(
        user=detail,
        message=(
            f"user '{body.id}' written to users.yaml; "
            f"reconcile={info['reconcile']}"
        ),
    )


# -- enable / disable ---------------------------------------------------------


@router.post("/users/{user_id}/enable", response_model=UserMutateResponse)
async def enable_user(
    user_id: str, request: Request, enabled: bool = True
) -> UserMutateResponse:
    try:
        set_enabled(user_id, enabled)
    except UsersYamlError as exc:
        raise HTTPException(404, str(exc)) from exc

    info = await _reconcile(request, user_id, wait_for_reachable=enabled)
    entry = get_user(user_id)
    assert entry is not None  # we just wrote it
    return UserMutateResponse(
        user=_detail(entry, reachable=info["agent_reachable"]),
        message=f"user '{user_id}' enabled={enabled}",
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
    """Opt-in/out background theme worker for a user (SIGHUP memory-supervisor)."""
    if get_user(user_id) is None:
        raise HTTPException(404, f"unknown user: {user_id!r}")

    cfg = ConsolidatorConfig(
        enabled=body.enabled,
        interval_hours=body.interval_hours,
        window_days=body.window_days,
        min_drawers=body.min_drawers,
        min_confidence=body.min_confidence,
    )
    try:
        set_consolidator(user_id, cfg)
    except UsersYamlError as exc:
        raise HTTPException(400, str(exc)) from exc

    entry = get_user(user_id)
    assert entry is not None
    info = await _reconcile(request, user_id, wait_for_reachable=entry.enabled)
    return UserMutateResponse(
        user=_detail(entry, reachable=info["agent_reachable"]),
        message=f"user '{user_id}' consolidator enabled={body.enabled}",
    )


@router.delete("/users/{user_id}/consolidator", response_model=UserMutateResponse)
async def remove_user_consolidator(user_id: str, request: Request) -> UserMutateResponse:
    if get_user(user_id) is None:
        raise HTTPException(404, f"unknown user: {user_id!r}")
    try:
        set_consolidator(user_id, None)
    except UsersYamlError as exc:
        raise HTTPException(400, str(exc)) from exc

    entry = get_user(user_id)
    assert entry is not None
    info = await _reconcile(request, user_id, wait_for_reachable=entry.enabled)
    return UserMutateResponse(
        user=_detail(entry, reachable=info["agent_reachable"]),
        message=f"user '{user_id}' consolidator block removed",
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
    entry = get_user(user_id)
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
