"""Trigger the appropriate reload action after a config save.

Reload modes (declared on each config in services.yaml):

  sighup_program  — SIGHUP a single program (e.g. memory-supervisor reloads
                    users.yaml on SIGHUP)
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
import time
from typing import Any

from ..supervisor.client import SupervisorClient, SupervisorError, SupervisorUnavailable

logger = logging.getLogger(__name__)


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
    try:
        await client.stop_process(target, wait=True)
    except SupervisorError:
        # Maybe already stopped; carry on.
        pass
    started = await client.start_process(target, wait=True)
    return {"restarted": bool(started)}


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
