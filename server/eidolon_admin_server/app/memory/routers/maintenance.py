"""Memory maintenance endpoints.

These proxy admin-facing actions to eidolon_memory's supervisor-embedded
HTTP API, so the browser talks only to eidolon_admin.
"""
from __future__ import annotations

from typing import Any, Protocol

from fastapi import APIRouter, HTTPException, Request
from eidolon_sdk.core.http import ServiceUnavailable, ServiceUpstreamError

from ..space import memory_space_id_for_realm
from ..schemas import MemoryReconcileResponse, RebuildIndexJob, RebuildIndexJobsResponse

router = APIRouter()


class MemoryMaintenanceClient(Protocol):
    async def reconcile(self) -> dict[str, Any]: ...

    async def cleanup_orphaned_realm(
        self,
        memory_realm_id: str,
        *,
        purge_palace: bool = False,
    ) -> dict[str, Any]: ...

    async def rebuild_index(self, memory_realm_id: str) -> dict[str, Any]: ...

    async def get_rebuild_index_job(self, job_id: str) -> dict[str, Any]: ...

    async def list_rebuild_index_jobs(self, memory_realm_id: str) -> dict[str, Any]: ...


def _memory_client(request: Request) -> MemoryMaintenanceClient:
    client = getattr(request.app.state, "memory_supervisor_client", None)
    if client is None:
        raise HTTPException(
            503,
            "memory supervisor client unavailable; admin booted without memory supervisor URL",
        )
    return client


def _map_upstream(exc: ServiceUpstreamError) -> HTTPException:
    detail: Any = exc.message
    return HTTPException(status_code=exc.status_code, detail=detail)


@router.post("/supervisor/reconcile", response_model=MemoryReconcileResponse)
async def reconcile_memory_supervisor(request: Request) -> MemoryReconcileResponse:
    try:
        return MemoryReconcileResponse.model_validate(
            await _memory_client(request).reconcile()
        )
    except ServiceUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    except ServiceUpstreamError as exc:
        raise _map_upstream(exc) from exc


@router.delete("/realms/{memory_realm_id}/orphan")
async def cleanup_orphaned_realm(
    memory_realm_id: str,
    request: Request,
    purge_palace: bool = False,
) -> dict[str, Any]:
    try:
        return await _memory_client(request).cleanup_orphaned_realm(
            memory_realm_id,
            purge_palace=purge_palace,
        )
    except ServiceUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    except ServiceUpstreamError as exc:
        raise _map_upstream(exc) from exc


@router.post(
    "/realms/{memory_realm_id}/rebuild-index",
    response_model=RebuildIndexJob,
    status_code=202,
)
async def rebuild_realm_index(memory_realm_id: str, request: Request) -> RebuildIndexJob:
    try:
        space_id = await memory_space_id_for_realm(request, memory_realm_id)
        return RebuildIndexJob.model_validate(
            await _memory_client(request).rebuild_index(space_id)
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
    "/realms/{memory_realm_id}/rebuild-index",
    response_model=RebuildIndexJobsResponse,
)
async def list_realm_rebuild_index_jobs(
    memory_realm_id: str,
    request: Request,
) -> RebuildIndexJobsResponse:
    try:
        space_id = await memory_space_id_for_realm(request, memory_realm_id)
        return RebuildIndexJobsResponse.model_validate(
            await _memory_client(request).list_rebuild_index_jobs(space_id)
        )
    except ServiceUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    except ServiceUpstreamError as exc:
        raise _map_upstream(exc) from exc
