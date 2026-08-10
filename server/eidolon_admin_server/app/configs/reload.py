"""Trigger the appropriate reload action after a config save.

Reload modes (declared on each config in services.yaml):

  sighup_program  — SIGHUP a single program (e.g. memory-supervisor reconciles
                    admin registry on SIGHUP)
  restart_program — supervisor stop + start one program
  restart_group   — supervisor stop + start a whole group (atomic for sets)
  none            — caller has to act (e.g. admin's own services.yaml needs
                    admin api restart)

Each function returns a small dict describing what happened so the frontend
can show "restarted N programs in M seconds" feedback.
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from ..supervisor.client import SupervisorClient, SupervisorError, SupervisorUnavailable

logger = logging.getLogger(__name__)


# Admin can request a process action, but the executable and process graph are
# owned by Ops.  Resolve the same roots exported by the Host profile instead of
# reaching back into Admin's repository layout.
_ADMIN_ROOT = Path(__file__).resolve().parents[4]
_OPS_ROOT = Path(
    os.environ.get("EIDOLON_OPS_ROOT", str(_ADMIN_ROOT.parent / "eidolon_ops"))
).expanduser().resolve()
_OPS_VENV = Path(
    os.environ.get("EIDOLON_OPS_VENV", str(_OPS_ROOT / ".venv"))
).expanduser().resolve()
_OPS_PYTHON = _OPS_VENV / "bin" / "python"
_SUPERVISORCTL = _OPS_VENV / "bin" / "supervisorctl"
_SUPERVISORD_CONF = _OPS_ROOT / "deploy" / "dev" / "supervisord.conf"


async def trigger(
    client: SupervisorClient,
    mode: str,
    target: str | None,
) -> dict[str, Any]:
    started = time.perf_counter()
    if mode == "none" or not mode:
        return {"mode": "none", "message": "config saved; reload requires manual action"}
    if not target:
        return {"mode": mode, "error": "reload target not configured"}

    try:
        if mode == "sighup_program":
            result = await _sighup(client, target)
        elif mode == "restart_program":
            result = await _restart_program(client, target)
        elif mode == "restart_group":
            result = await _restart_group(client, target)
        else:
            return {"mode": mode, "error": f"unknown reload mode: {mode}"}
    except SupervisorUnavailable as exc:
        return {"mode": mode, "target": target, "error": f"supervisord unreachable: {exc}"}
    except SupervisorError as exc:
        return {"mode": mode, "target": target, "error": str(exc)}

    elapsed = round((time.perf_counter() - started) * 1000)
    return {"mode": mode, "target": target, "duration_ms": elapsed, **result}


async def _sighup(client: SupervisorClient, target: str) -> dict[str, Any]:
    ok = await client._call("supervisor.signalProcess", target, "HUP")  # noqa: SLF001
    return {"signaled": bool(ok)}


async def _restart_program(client: SupervisorClient, target: str) -> dict[str, Any]:
    # Self-restart deadlock guard.
    #
    # If the program we're being asked to restart is the admin-api process
    # itself, an in-process supervisor.stopProcess(wait=True) call deadlocks:
    #   - supervisord SIGTERMs admin-api
    #   - admin-api's uvicorn refuses to exit while the /reload request is
    #     still in flight
    #   - the /reload request is blocked waiting for stopProcess to return
    #   - 14s later supervisord SIGKILLs the process
    #   - supervisord then treats this as a controlled stop (RPC-initiated),
    #     so autorestart doesn't fire — admin-api stays STOPPED forever
    # Sidestep this by spawning a detached supervisorctl that performs the
    # restart from outside admin-api's process tree.
    if await _is_self(client, target):
        return _self_restart(target)

    try:
        await client.stop_process(target, wait=True)
    except SupervisorError:
        # Maybe already stopped; carry on.
        pass
    started = await client.start_process(target, wait=True)
    return {"restarted": bool(started)}


async def _is_self(client: SupervisorClient, target: str) -> bool:
    """True if ``target`` is the admin-api program (us)."""
    try:
        info = await client.get_process_info(target)
    except (SupervisorError, SupervisorUnavailable):
        return False
    return info.pid == os.getpid()


def _self_restart(target: str) -> dict[str, Any]:
    """Spawn a fully-detached helper that restarts admin-api from outside.

    The helper sleeps briefly to let this request flush its 200 OK, then
    invokes supervisorctl to do stop+start. Because the helper has no
    parent here (detached via ``start_new_session``) it survives admin-api
    being SIGTERM'd by supervisord.
    """
    missing = [
        path
        for path in (_OPS_PYTHON, _SUPERVISORCTL, _SUPERVISORD_CONF)
        if not path.is_file()
    ]
    if missing:
        return {
            "self_restart": False,
            "error": "Ops restart executor is incomplete: "
            + ", ".join(str(path) for path in missing),
        }
    cmd = [
        str(_OPS_PYTHON),
        "-m",
        "eidolon_admin_server.app.configs.restart_helper",
        "--supervisorctl",
        str(_SUPERVISORCTL),
        "--config",
        str(_SUPERVISORD_CONF),
        "--target",
        target,
    ]
    # start_new_session decouples the helper from our process group so a
    # SIGTERM landing on admin-api doesn't cascade into it. stdin/out/err
    # are closed via DEVNULL — supervisorctl prints to a terminal that
    # doesn't exist anyway once we're gone.
    subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    logger.info("self-restart scheduled via detached Ops executor: %s", target)
    return {
        "self_restart": True,
        "message": (
            "admin-api restart scheduled — this connection will drop in ~0.5s; "
            "the supervisor will bring the api back up automatically."
        ),
    }


async def _restart_group(client: SupervisorClient, target: str) -> dict[str, Any]:
    stop_results = []
    try:
        stop_results = await client.stop_process_group(target, wait=True)
    except SupervisorError:
        pass
    # Tiny gap to let sockets release before re-binding.
    await asyncio.sleep(0.5)
    start_results = await client.start_process_group(target, wait=True)
    return {"stopped": stop_results, "started": start_results}
