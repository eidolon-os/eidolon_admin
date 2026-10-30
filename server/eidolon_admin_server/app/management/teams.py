"""Credential-isolated bridge to the existing Data workspace authority."""
from eidolon_sdk.biz.control.coordination import RoleGroupStatus
from fastapi import APIRouter, Depends, Query, Request
from eidolon_sdk.biz.control.teams import TeamPage, TeamRevision, TeamWrite, TeamDeleted, TeamStartRequest
from eidolon_admin_server.app.service_auth import require_local_api_credential

router = APIRouter(prefix="/internal/v1/management/teams",
    dependencies=[Depends(require_local_api_credential)])


@router.get("", response_model=TeamPage)
async def listing(request: Request, owner_id: str, host_id: str,
                  after: str = "", limit: int = Query(default=50, ge=1, le=100)):
    return await request.app.state.control_plane.workspace.teams(
        owner_id, host_id=host_id, after=after, limit=limit)


@router.get("/{team_id}", response_model=TeamRevision)
async def get(request: Request, owner_id: str, host_id: str, team_id: str):
    return await request.app.state.control_plane.workspace.teams(
        owner_id, host_id=host_id, team_id=team_id)


@router.put("/{team_id}", response_model=TeamRevision)
async def save(request: Request, owner_id: str, host_id: str, team_id: str, body: TeamWrite):
    return await request.app.state.control_plane.workspace.teams(owner_id,
        host_id=host_id, team_id=team_id, method="PUT", payload=body.model_dump(mode="json"))


@router.delete("/{team_id}", response_model=TeamDeleted)
async def delete(request: Request, owner_id: str, host_id: str, team_id: str,
                 expected_revision: int = Query(ge=1)):
    return await request.app.state.control_plane.workspace.teams(owner_id,
        host_id=host_id, team_id=team_id, method="DELETE", expected_revision=expected_revision)


@router.post("/{team_id}/starts/reserve")
async def reserve_start(request: Request, owner_id: str, host_id: str, team_id: str,
                        body: TeamStartRequest):
    return await request.app.state.control_plane.workspace.team_starts(owner_id,
        host_id=host_id, team_id=team_id, action="reserve", payload=body.model_dump(mode="json"))


@router.post("/{team_id}/starts/list")
async def starts(request: Request, owner_id: str, host_id: str, team_id: str,
                 include_closed: bool = False, after: str = "", limit: int = Query(default=50, ge=1, le=100)):
    return await request.app.state.control_plane.workspace.team_starts(owner_id,
        host_id=host_id, team_id=team_id, action="list",
        include_closed=include_closed, after=after, limit=limit)


@router.post("/{team_id}/starts/confirmed-closed")
async def confirm_closed(request: Request, owner_id: str, host_id: str, team_id: str, session_id: str,
                         body: RoleGroupStatus | None = None):
    return await request.app.state.control_plane.workspace.team_starts(owner_id,
        host_id=host_id, team_id=team_id, action="confirmed-closed", session_id=session_id,
        payload=body.model_dump(mode="json") if body is not None else None)


@router.post("/{team_id}/starts/get")
async def get_start(request: Request, owner_id: str, host_id: str, team_id: str, session_id: str):
    return await request.app.state.control_plane.workspace.team_starts(owner_id,
        host_id=host_id, team_id=team_id, session_id=session_id, action="get")


from eidolon_sdk.biz.control.coordination import CoordinationSelection


@router.post("/{team_id}/starts/snapshot")
async def freeze_start(request: Request, owner_id: str, host_id: str, team_id: str,
                       session_id: str, body: CoordinationSelection):
    return await request.app.state.control_plane.workspace.team_starts(owner_id,
        host_id=host_id, team_id=team_id, session_id=session_id, action="snapshot",
        payload=body.model_dump(mode="json"))
