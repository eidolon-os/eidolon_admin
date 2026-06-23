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


# Repo root: this file is at
# server/eidolon_admin_server/app/configs/reload.py, so parents[4] is the
# project root. Used to locate supervisorctl + supervisord.conf when we
# need to spawn an external restart helper for the admin-api self-restart
# case (see ``_self_restart`` below).
_REPO_ROOT = Path(__file__).resolve().parents[4]
_SUPERVISORCTL = _REPO_ROOT / ".venv" / "bin" / "supervisorctl"
_SUPERVISORD_CONF = _REPO_ROOT / "deploy" / "dev" / "supervisord.conf"


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
    if not _SUPERVISORCTL.exists():
        return {
            "self_restart": False,
            "error": f"supervisorctl not found at {_SUPERVISORCTL}",
        }
    cmd = [
        "/bin/sh",
        "-c",
        f"sleep 0.5 && exec {_SUPERVISORCTL} -c {_SUPERVISORD_CONF} "
        f"restart {target} >/dev/null 2>&1",
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
    logger.info("self-restart scheduled via detached supervisorctl: %s", target)
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
