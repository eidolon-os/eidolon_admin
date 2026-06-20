"""Memory maintenance endpoints.

These proxy admin-facing actions to eidolon_memory's supervisor-embedded
HTTP API, so the browser talks only to eidolon_admin.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from eidolon_sdk.http import ServiceUnavailable, ServiceUpstreamError

from ...registry.users.repository import MemoryUserClient
from ..schemas import RebuildIndexJob, RebuildIndexJobsResponse

router = APIRouter()


def _memory_client(request: Request) -> MemoryUserClient:
    client: MemoryUserClient | None = getattr(
        request.app.state, "memory_user_client", None
    )
    if client is None:
        raise HTTPException(
            503,
            "memory supervisor client unavailable; admin booted without memory supervisor URL",
        )
    return client


def _map_upstream(exc: ServiceUpstreamError) -> HTTPException:
    detail: Any = exc.message
    return HTTPException(status_code=exc.status_code, detail=detail)


@router.post(
    "/users/{user_id}/rebuild-index",
    response_model=RebuildIndexJob,
    status_code=202,
)
async def rebuild_user_index(user_id: str, request: Request) -> RebuildIndexJob:
    try:
        return RebuildIndexJob.model_validate(
            await _memory_client(request).rebuild_index(user_id)
        )
    except ServiceUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    except ServiceUpstreamError as exc:
        raise _map_upstream(exc) from exc


@router.get("/rebuild-index/{job_id}", response_model=RebuildIndexJob)
async def get_rebuild_index_job(job_id: str, request: Request) -> RebuildIndexJob:
    try:
        return RebuildIndexJob.model_validate(
            await _memory_client(request).get_rebuild_index_job(job_id)
        )
    except ServiceUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    except ServiceUpstreamError as exc:
        raise _map_upstream(exc) from exc


@router.get(
    "/users/{user_id}/rebuild-index",
    response_model=RebuildIndexJobsResponse,
)
async def list_user_rebuild_index_jobs(
    user_id: str,
    request: Request,
) -> RebuildIndexJobsResponse:
    try:
        return RebuildIndexJobsResponse.model_validate(
            await _memory_client(request).list_rebuild_index_jobs(user_id)
        )
    except ServiceUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    except ServiceUpstreamError as exc:
        raise _map_upstream(exc) from exc
