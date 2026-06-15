"""FastAPI router for ``/api/users/*``.

HTTP I/O only. Matches the tenants/templates pattern; the orchestrator
has already mapped memory's status codes into admin's exception
classes.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..schemas.user import (
    CreateUserRequest,
    SetActiveAgentRequest,
    UpdateUserRequest,
    UserListResponse,
    UserSpec,
    UserView,
)
from .orchestrator import UserError, UserMemoryDown, UserOrchestrator

router = APIRouter(prefix="/users", tags=["users"])


def _orchestrator(request: Request) -> UserOrchestrator:
    orch: UserOrchestrator | None = getattr(
        request.app.state, "user_orchestrator", None
    )
    if orch is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "user orchestrator unavailable — admin booted without "
                "memory service URL or registry init failed"
            ),
        )
    return orch


@router.get("", response_model=UserListResponse)
async def list_users(request: Request) -> UserListResponse:
    orch = _orchestrator(request)
    try:
        users = await orch.list_users()
        return UserListResponse(users=users, memory_available=True)
    except UserMemoryDown:
        # Same envelope pattern as templates: keep the surface usable so
        # UI banners "memory unavailable" rather than crashing.
        return UserListResponse(users=[], memory_available=False)
    except UserError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("/registry")
async def list_user_registry(request: Request) -> dict[str, list[UserSpec]]:
    """Pure admin user registry for execution projects.

    No memory health enrichment here; memory consumes this endpoint.
    """
    orch = _orchestrator(request)
    try:
        return {"users": await orch.list_registry_specs()}
    except UserError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("/{user_id}", response_model=UserView)
async def get_user(user_id: str, request: Request) -> UserView:
    orch = _orchestrator(request)
    try:
        return await orch.get_user(user_id)
    except UserError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("", response_model=UserView, status_code=201)
async def create_user(body: CreateUserRequest, request: Request) -> UserView:
    orch = _orchestrator(request)
    try:
        return await orch.create_user(body)
    except UserError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.put("/{user_id}", response_model=UserView)
async def update_user(
    user_id: str, body: UpdateUserRequest, request: Request
) -> UserView:
    orch = _orchestrator(request)
    try:
        return await orch.update_user(user_id, body)
    except UserError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/{user_id}/enable", response_model=UserView)
async def set_user_enabled(
    user_id: str, request: Request, enabled: bool = True
) -> UserView:
    """Project-wide user switch.

    Admin persists the flag in the registry DB; memory only reconciles to it.
    """
    orch = _orchestrator(request)
    try:
        return await orch.set_user_enabled(user_id, enabled)
    except UserError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/{user_id}/set-active-agent", response_model=UserView)
async def set_active_agent(
    user_id: str, body: SetActiveAgentRequest, request: Request
) -> UserView:
    orch = _orchestrator(request)
    try:
        return await orch.set_active_agent(user_id, body)
    except UserError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.delete("/{user_id}", status_code=200)
async def delete_user(user_id: str, request: Request) -> dict:
    """200 (not 204) because we return memory's response envelope —
    ``palace_trashed_to`` etc. so admin UI can show the trashcan path."""
    orch = _orchestrator(request)
    try:
        return await orch.delete_user(user_id)
    except UserError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/{user_id}/revoke-sessions", status_code=200)
async def revoke_user_sessions(user_id: str, request: Request) -> dict:
    """Phase 33.B1: invalidate all active runtime tokens for the user.

    Admin **proxies** to agent's revocation surface — agent owns the
    NATS ``DEVICE_REVOCATIONS`` bucket. After this call, every JWT
    carrying this user_id fails verification on the next chat() turn
    with ``TokenRevokedError`` → LK session aborts. New connections
    will only succeed if the user record is also re-enabled.

    Returns agent's echo: ``{user_id, revoked: true}``.

    Failure modes:
      - admin's user_id not found → 404 (validation before proxying)
      - agent unreachable → 503
      - agent revocation bucket missing → 503 (agent surfaces this)
    """
    # First confirm the user exists in admin's registry. Without this
    # check we'd happily revoke a typo (writes a stray KV key that
    # forever blocks the imaginary user).
    orch = _orchestrator(request)
    try:
        await orch.get_user(user_id)
    except UserError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    agent_orch = getattr(request.app.state, "agent_orchestrator", None)
    if agent_orch is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "agent orchestrator unavailable — cannot reach the "
                "revocation surface. Check agent service health."
            ),
        )
    # Reach into the agent's project client. We don't bother going
    # through an orchestrator method because this is a thin RPC proxy
    # with no admin-side state mutation.
    from ..agents.repository import (
        AgentProjectUnreachable,
        AgentProjectUpstreamError,
    )

    try:
        return await agent_orch._agent.revoke_user_sessions(user_id)
    except AgentProjectUnreachable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except AgentProjectUpstreamError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"agent upstream: {exc.message}",
        ) from exc
