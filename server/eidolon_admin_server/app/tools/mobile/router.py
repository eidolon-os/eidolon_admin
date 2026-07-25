from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from .schemas import (
    MobileDevicesResponse,
    MobileEnvironmentStatus,
    MobileJob,
    MobileJobRequest,
    MobileJobsResponse,
)
from .service import (
    MobileJobConflict,
    MobileNotFound,
    MobileToolError,
    MobileToolService,
)


router = APIRouter(prefix="/tools/mobile", tags=["tools:mobile"])


def _service(request: Request) -> MobileToolService:
    return request.app.state.mobile_tools


@router.get("/devices", response_model=MobileDevicesResponse)
async def list_devices(request: Request) -> MobileDevicesResponse:
    return MobileDevicesResponse(devices=_service(request).devices())


@router.get("/environment", response_model=MobileEnvironmentStatus)
async def get_environment(
    request: Request,
    mode: str = Query(default="debug", pattern="^(debug|profile|release)$"),
) -> MobileEnvironmentStatus:
    return _service(request).environment(mode)


@router.post("/jobs", response_model=MobileJob, status_code=202)
async def create_job(req: MobileJobRequest, request: Request) -> MobileJob:
    try:
        return await _service(request).create_job(req)
    except MobileNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except MobileJobConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except MobileToolError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/jobs", response_model=MobileJobsResponse)
async def list_jobs(request: Request) -> MobileJobsResponse:
    return MobileJobsResponse(jobs=_service(request).list_jobs())


@router.get("/jobs/{job_id}", response_model=MobileJob)
async def get_job(job_id: str, request: Request) -> MobileJob:
    try:
        return _service(request).get_job(job_id)
    except MobileNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/cancel", response_model=MobileJob)
async def cancel_job(job_id: str, request: Request) -> MobileJob:
    try:
        return await _service(request).cancel_job(job_id)
    except MobileNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/jobs/{job_id}/stream")
async def stream_job(job_id: str, request: Request) -> StreamingResponse:
    async def _events() -> AsyncIterator[bytes]:
        try:
            async for line in _service(request).stream_job(job_id):
                yield _sse(line)
        except MobileNotFound as exc:
            yield _sse(f"[error] {exc}")

    return StreamingResponse(_events(), media_type="text/event-stream")


@router.get("/logs/stream")
async def stream_logs(
    request: Request,
    serial: str = Query(..., min_length=1),
) -> StreamingResponse:
    async def _events() -> AsyncIterator[bytes]:
        try:
            async for line in _service(request).log_stream(serial):
                yield _sse(line)
        except (MobileNotFound, MobileToolError) as exc:
            yield _sse(f"[error] {exc}")

    return StreamingResponse(_events(), media_type="text/event-stream")


def _sse(line: str) -> bytes:
    safe = line.replace("\r", "").replace("\n", "\\n")
    return f"data: {safe}\n\n".encode()
