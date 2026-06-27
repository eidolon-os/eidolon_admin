from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from .schemas import (
    Esp32BoardInfo,
    Esp32BoardsResponse,
    Esp32EnvironmentStatus,
    Esp32Job,
    Esp32JobRequest,
    Esp32JobsResponse,
    Esp32PortsResponse,
)
from .service import Esp32JobConflict, Esp32NotFound, Esp32ToolError, Esp32ToolService

router = APIRouter(prefix="/tools/esp32", tags=["tools:esp32"])


def _service(request: Request) -> Esp32ToolService:
    return request.app.state.esp32_tools


@router.get("/boards", response_model=Esp32BoardsResponse)
async def list_boards(request: Request) -> Esp32BoardsResponse:
    return Esp32BoardsResponse(boards=_service(request).boards())


@router.get("/ports", response_model=Esp32PortsResponse)
async def list_ports(request: Request) -> Esp32PortsResponse:
    return Esp32PortsResponse(ports=_service(request).ports())


@router.get("/environment", response_model=Esp32EnvironmentStatus)
async def get_environment(request: Request) -> Esp32EnvironmentStatus:
    return _service(request).environment()


@router.get("/boards/{board_id}/info", response_model=Esp32BoardInfo)
async def get_board_info(board_id: str, request: Request) -> Esp32BoardInfo:
    try:
        return _service(request).board_info(board_id)
    except Esp32NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/jobs", response_model=Esp32Job, status_code=202)
async def create_job(req: Esp32JobRequest, request: Request) -> Esp32Job:
    try:
        return await _service(request).create_job(req)
    except Esp32NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Esp32JobConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Esp32ToolError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/jobs", response_model=Esp32JobsResponse)
async def list_jobs(request: Request) -> Esp32JobsResponse:
    return Esp32JobsResponse(jobs=_service(request).list_jobs())


@router.get("/jobs/{job_id}", response_model=Esp32Job)
async def get_job(job_id: str, request: Request) -> Esp32Job:
    try:
        return _service(request).get_job(job_id)
    except Esp32NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/cancel", response_model=Esp32Job)
async def cancel_job(job_id: str, request: Request) -> Esp32Job:
    try:
        return await _service(request).cancel_job(job_id)
    except Esp32NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/jobs/{job_id}/stream")
async def stream_job(job_id: str, request: Request) -> StreamingResponse:
    async def _events() -> AsyncIterator[bytes]:
        try:
            async for line in _service(request).stream_job(job_id):
                yield _sse(line)
        except Esp32NotFound as exc:
            yield _sse(f"[error] {exc}")

    return StreamingResponse(_events(), media_type="text/event-stream")


@router.get("/serial/stream")
async def serial_stream(
    request: Request,
    board_id: str = Query(...),
    port: str = Query(...),
    baud: int | None = Query(default=None, ge=1, le=2_000_000),
) -> StreamingResponse:
    async def _events() -> AsyncIterator[bytes]:
        try:
            async for line in _service(request).serial_stream(board_id, port, baud):
                yield _sse(line)
        except Esp32NotFound as exc:
            yield _sse(f"[error] {exc}")
        except Esp32ToolError as exc:
            yield _sse(f"[error] {exc}")

    return StreamingResponse(_events(), media_type="text/event-stream")


def _sse(line: str) -> bytes:
    safe = line.replace("\r", "").replace("\n", "\\n")
    return f"data: {safe}\n\n".encode()

