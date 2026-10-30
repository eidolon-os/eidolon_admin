"""Saved teams in the authenticated Host/Owner scope; neither is user supplied."""
from fastapi import APIRouter, Header, Query, HTTPException
from eidolon_sdk.biz.control.teams import TeamPage, TeamRevision, TeamWrite, TeamDeleted, TeamStartRequest, TeamStarts, TeamConflicts
from .router import ManagementBackendError, _refused


def register_team_routes(app, *, backend, authenticated_owner, authenticated_host,
                         authenticate_session=None, devices=None):
    router = APIRouter(prefix="/api/management/v1/teams", tags=["management"])

    async def invoke(authorization, **kwargs):
        owner = await authenticated_owner(authorization)
        host = await authenticated_host()
        try:
            return await backend.teams(owner_id=owner, host_id=host, **kwargs)
        except ManagementBackendError as exc:
            raise _refused(exc) from exc

    @router.get("", response_model=TeamPage)
    async def listing(after: str = "", limit: int = Query(default=50, ge=1, le=100),
                      authorization: str | None = Header(default=None)):
        return await invoke(authorization, after=after, limit=limit)

    @router.get("/{team_id}", response_model=TeamRevision)
    async def get(team_id: str, authorization: str | None = Header(default=None)):
        return await invoke(authorization, team_id=team_id)

    @router.put("/{team_id}", response_model=TeamRevision)
    async def save(team_id: str, body: TeamWrite,
                   authorization: str | None = Header(default=None)):
        return await invoke(authorization, team_id=team_id, method="PUT",
                            payload=body.model_dump(mode="json"))

    @router.delete("/{team_id}", response_model=TeamDeleted)
    async def delete(team_id: str, expected_revision: int = Query(ge=1),
                     authorization: str | None = Header(default=None)):
        return await invoke(authorization, team_id=team_id, method="DELETE",
                            expected_revision=expected_revision)

    if authenticate_session is not None and devices is not None:
        from .team_runs import SavedTeamRuns
        from eidolon_sdk.biz.control.coordination import RoleGroupStatus
        runs = SavedTeamRuns(backend, devices)

        async def run_command(authorization, team_id, *, request=None, session_id=None, close=False):
            session = await authenticate_session(authorization)
            host_id = await authenticated_host()
            try:
                if request is not None:
                    return await runs.start(session=session, host_id=host_id,
                                            team_id=team_id, request=request)
                return await runs.inspect(session=session, host_id=host_id,
                    team_id=team_id, session_id=session_id, close=close)
            except ManagementBackendError as exc:
                raise _refused(exc) from exc
            except ValueError as exc:
                raise HTTPException(409, detail={"code": str(exc)}) from exc

        @router.get("/{team_id}/conflicts", response_model=TeamConflicts)
        async def conflicts(team_id: str, authorization: str | None = Header(default=None)):
            session = await authenticate_session(authorization)
            host_id = await authenticated_host()
            try:
                return await runs.conflicts(session=session, host_id=host_id, team_id=team_id)
            except ManagementBackendError as exc:
                raise _refused(exc) from exc
            except TimeoutError as exc:
                raise HTTPException(503, detail={"code": "TEAM_CONFLICT_STATUS_UNAVAILABLE"}) from exc

        @router.post("/{team_id}/runs", response_model=RoleGroupStatus)
        async def start(team_id: str, body: TeamStartRequest,
                        authorization: str | None = Header(default=None)):
            return await run_command(authorization, team_id, request=body)

        @router.get("/{team_id}/runs", response_model=TeamStarts)
        async def active_runs(team_id: str, include_closed: bool = False, after: str = "",
                              limit: int = Query(default=50, ge=1, le=100),
                              authorization: str | None = Header(default=None)):
            session = await authenticate_session(authorization)
            host_id = await authenticated_host()
            try:
                return await backend.team_starts(owner_id=session.owner_id,
                    host_id=host_id, team_id=team_id, action="list",
                    include_closed=include_closed, after=after, limit=limit)
            except ManagementBackendError as exc:
                raise _refused(exc) from exc

        @router.get("/{team_id}/runs/{session_id}", response_model=RoleGroupStatus)
        async def inspect(team_id: str, session_id: str,
                          authorization: str | None = Header(default=None)):
            return await run_command(authorization, team_id, session_id=session_id)

        @router.post("/{team_id}/runs/{session_id}/close", response_model=RoleGroupStatus)
        async def close(team_id: str, session_id: str,
                        authorization: str | None = Header(default=None)):
            return await run_command(authorization, team_id, session_id=session_id, close=True)

    app.include_router(router)
