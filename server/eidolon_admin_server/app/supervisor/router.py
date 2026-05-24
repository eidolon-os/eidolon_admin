"""REST surface for the supervisor module.

Routes (mounted at /api/supervisor):
    GET    /programs                       list every program known to supervisord
    GET    /programs/{name}                detail for one program (group:name)
    POST   /programs/{name}/start
    POST   /programs/{name}/stop
    POST   /programs/{name}/restart
    POST   /groups/{group}/start
    POST   /groups/{group}/stop
    GET    /programs/{name}/logs           tail stdout/stderr
    GET    /programs/{name}/logs/stream    SSE follow

    GET    /configs                        list available/ configs + enabled flag
    GET    /configs/{name}                 read .conf text + parsed sections
    PUT    /configs/{name}                 overwrite the .conf text
    POST   /configs/{name}/enable          create enabled/ symlink + reread + update
    POST   /configs/{name}/disable         stop + delete symlink + reread + update
    POST   /reread                         supervisor.reloadConfig + addProcessGroup
    GET    /state                          { ping: bool, state: {...} }
"""
from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .client import (
    ProcessInfo,
    SupervisorClient,
    SupervisorError,
    SupervisorUnavailable,
)
from .config import ConfigError, ConfigStore

router = APIRouter(prefix="/supervisor", tags=["supervisor"])


def _client(request: Request) -> SupervisorClient:
    return request.app.state.supervisor_client


def _configs(request: Request) -> ConfigStore:
    return request.app.state.supervisor_configs


def _process_payload(info: ProcessInfo) -> dict[str, Any]:
    return {
        "name": info.name,
        "group": info.group,
        "full_name": info.full_name,
        "state": info.state,
        "statename": info.statename,
        "pid": info.pid,
        "start": info.start,
        "stop": info.stop,
        "now": info.now,
        "exitstatus": info.exitstatus,
        "description": info.description,
        "spawnerr": info.spawnerr,
        "stdout_logfile": info.logfile,
        "stderr_logfile": info.stderr_logfile,
    }


# ---------- state --------------------------------------------------------


@router.get("/state")
async def get_state(request: Request) -> dict[str, Any]:
    client = _client(request)
    ping = await client.ping()
    state: dict[str, Any] | None = None
    if ping:
        try:
            state = await client.get_state()
        except SupervisorError:
            state = None
    return {
        "ping": ping,
        "socket": str(client.socket_path),
        "state": state,
    }


# ---------- programs -----------------------------------------------------


@router.get("/programs")
async def list_programs(request: Request) -> dict[str, Any]:
    client = _client(request)
    try:
        infos = await client.get_all_process_info()
    except SupervisorUnavailable as exc:
        raise HTTPException(503, str(exc))
    return {"programs": [_process_payload(i) for i in infos]}


@router.get("/programs/{name:path}")
async def get_program(name: str, request: Request) -> dict[str, Any]:
    client = _client(request)
    try:
        info = await client.get_process_info(name)
    except SupervisorUnavailable as exc:
        raise HTTPException(503, str(exc))
    except SupervisorError as exc:
        raise HTTPException(404, str(exc))
    return _process_payload(info)


class _WaitBody(BaseModel):
    wait: bool = True


@router.post("/programs/{name:path}/start")
async def start_program(name: str, request: Request, body: _WaitBody = _WaitBody()) -> dict[str, Any]:
    try:
        ok = await _client(request).start_process(name, wait=body.wait)
    except SupervisorUnavailable as exc:
        raise HTTPException(503, str(exc))
    except SupervisorError as exc:
        raise HTTPException(400, str(exc))
    return {"started": ok, "name": name}


@router.post("/programs/{name:path}/stop")
async def stop_program(name: str, request: Request, body: _WaitBody = _WaitBody()) -> dict[str, Any]:
    try:
        ok = await _client(request).stop_process(name, wait=body.wait)
    except SupervisorUnavailable as exc:
        raise HTTPException(503, str(exc))
    except SupervisorError as exc:
        raise HTTPException(400, str(exc))
    return {"stopped": ok, "name": name}


@router.post("/programs/{name:path}/restart")
async def restart_program(name: str, request: Request) -> dict[str, Any]:
    client = _client(request)
    try:
        try:
            await client.stop_process(name, wait=True)
        except SupervisorError:
            # Already stopped — fine, just go to start.
            pass
        ok = await client.start_process(name, wait=True)
    except SupervisorUnavailable as exc:
        raise HTTPException(503, str(exc))
    except SupervisorError as exc:
        raise HTTPException(400, str(exc))
    return {"restarted": ok, "name": name}


@router.post("/groups/{group}/start")
async def start_group(group: str, request: Request) -> dict[str, Any]:
    try:
        results = await _client(request).start_process_group(group, wait=True)
    except SupervisorUnavailable as exc:
        raise HTTPException(503, str(exc))
    except SupervisorError as exc:
        raise HTTPException(400, str(exc))
    return {"group": group, "results": results}


@router.post("/groups/{group}/stop")
async def stop_group(group: str, request: Request) -> dict[str, Any]:
    try:
        results = await _client(request).stop_process_group(group, wait=True)
    except SupervisorUnavailable as exc:
        raise HTTPException(503, str(exc))
    except SupervisorError as exc:
        raise HTTPException(400, str(exc))
    return {"group": group, "results": results}


# ---------- logs ---------------------------------------------------------


@router.get("/programs/{name:path}/logs")
async def tail_log(
    name: str,
    request: Request,
    stream: Literal["stdout", "stderr"] = Query("stdout"),
    length: int = Query(16384, ge=1, le=1_048_576),
) -> dict[str, Any]:
    client = _client(request)
    try:
        if stream == "stdout":
            text, offset, overflow = await client.tail_process_stdout_log(name, 0, length)
        else:
            text, offset, overflow = await client.tail_process_stderr_log(name, 0, length)
    except SupervisorUnavailable as exc:
        raise HTTPException(503, str(exc))
    except SupervisorError as exc:
        raise HTTPException(404, str(exc))
    return {"name": name, "stream": stream, "text": text, "offset": offset, "overflow": overflow}


@router.get("/programs/{name:path}/logs/stream")
async def follow_log(
    name: str,
    request: Request,
    stream: Literal["stdout", "stderr"] = Query("stdout"),
):
    client = _client(request)

    async def _gen():
        offset = 0
        yield f"data: # tail -F {name} ({stream})\n\n".encode()
        # Seed with the last 8KB so the viewer has context immediately.
        try:
            if stream == "stdout":
                text, offset, _ = await client.tail_process_stdout_log(name, 0, 8192)
            else:
                text, offset, _ = await client.tail_process_stderr_log(name, 0, 8192)
            for line in text.splitlines():
                yield f"data: {line}\n\n".encode()
        except (SupervisorError, SupervisorUnavailable) as exc:
            yield f"data: [error] {exc}\n\n".encode()
            return

        while True:
            if await request.is_disconnected():
                return
            try:
                if stream == "stdout":
                    chunk, offset, _ = await client.tail_process_stdout_log(name, offset, 16384)
                else:
                    chunk, offset, _ = await client.tail_process_stderr_log(name, offset, 16384)
            except (SupervisorError, SupervisorUnavailable) as exc:
                yield f"data: [error] {exc}\n\n".encode()
                await asyncio.sleep(2.0)
                continue
            if chunk:
                for line in chunk.splitlines():
                    yield f"data: {line}\n\n".encode()
            else:
                await asyncio.sleep(0.8)

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------- configs ------------------------------------------------------


@router.get("/configs")
async def list_configs(request: Request) -> dict[str, Any]:
    entries = _configs(request).list()
    return {
        "configs": [
            {
                "name": e.name,
                "enabled": e.enabled,
                "available_path": str(e.available_path),
                "enabled_path": str(e.enabled_path),
                "programs": e.programs,
                "groups": e.groups,
            }
            for e in entries
        ]
    }


@router.get("/configs/{name}")
async def read_config(name: str, request: Request) -> dict[str, Any]:
    store = _configs(request)
    try:
        entry = store.get(name)
        text = store.read_text(name)
    except ConfigError as exc:
        raise HTTPException(404, str(exc))
    return {
        "name": entry.name,
        "enabled": entry.enabled,
        "programs": entry.programs,
        "groups": entry.groups,
        "text": text,
    }


class _ConfigBody(BaseModel):
    text: str = Field(..., min_length=1)


@router.put("/configs/{name}")
async def write_config(name: str, body: _ConfigBody, request: Request) -> dict[str, Any]:
    store = _configs(request)
    try:
        entry = await store.write_text(name, body.text)
    except ConfigError as exc:
        raise HTTPException(400, str(exc))
    return {
        "name": entry.name,
        "enabled": entry.enabled,
        "programs": entry.programs,
        "groups": entry.groups,
    }


@router.post("/configs/{name}/enable")
async def enable_config(name: str, request: Request) -> dict[str, Any]:
    store = _configs(request)
    client = _client(request)
    try:
        entry = await store.enable(name)
    except ConfigError as exc:
        raise HTTPException(404, str(exc))
    # Best-effort reread + update so the daemon picks up the new program(s).
    summary = None
    try:
        summary = await client.update()
    except SupervisorUnavailable:
        summary = {"warning": "supervisord not running; restart it to apply"}
    return {"enabled": entry.enabled, "name": name, "supervisord_update": summary}


@router.post("/configs/{name}/disable")
async def disable_config(name: str, request: Request) -> dict[str, Any]:
    store = _configs(request)
    client = _client(request)
    # Stop running programs from this config before pulling its definition.
    try:
        entry = store.get(name)
    except ConfigError as exc:
        raise HTTPException(404, str(exc))
    for group in entry.groups:
        try:
            await client.stop_process_group(group)
        except (SupervisorError, SupervisorUnavailable):
            pass
    try:
        entry = await store.disable(name)
    except ConfigError as exc:
        raise HTTPException(400, str(exc))
    summary = None
    try:
        summary = await client.update()
    except SupervisorUnavailable:
        summary = {"warning": "supervisord not running"}
    return {"enabled": entry.enabled, "name": name, "supervisord_update": summary}


@router.post("/reread")
async def reread(request: Request) -> dict[str, Any]:
    client = _client(request)
    try:
        return await client.update()
    except SupervisorUnavailable as exc:
        raise HTTPException(503, str(exc))
