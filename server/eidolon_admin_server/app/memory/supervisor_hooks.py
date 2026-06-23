"""SIGHUP memory-supervisor through supervisord's XML-RPC.

memory-supervisor reloads admin's registry view on SIGHUP. When admin mutates
users, we send the signal so memory-supervisor reconciles agent_runner and
consolidator subprocesses without an external nudge.
"""
from __future__ import annotations

import asyncio
import logging

from ..supervisor.client import SupervisorClient, SupervisorError, SupervisorUnavailable

logger = logging.getLogger(__name__)


MEMORY_SUPERVISOR_PROGRAM = "memory:memory-supervisor"


async def sighup_memory_supervisor(client: SupervisorClient) -> dict:
    """Send SIGHUP to memory-supervisor via supervisord's signalProcess.

    Returns a small status dict — never raises; the caller treats failure as
    "supervisor not running yet, registry changes are still persisted".
    """
    try:
        ok = await client._call(  # noqa: SLF001 — supervisor.signalProcess isn't in our wrapper
            "supervisor.signalProcess", MEMORY_SUPERVISOR_PROGRAM, "HUP"
        )
        return {"signaled": bool(ok), "program": MEMORY_SUPERVISOR_PROGRAM}
    except (SupervisorUnavailable, SupervisorError) as exc:
        logger.info("memory-supervisor SIGHUP skipped: %s", exc)
        return {
            "signaled": False,
            "program": MEMORY_SUPERVISOR_PROGRAM,
            "reason": str(exc),
        }


async def wait_for_user_reachable(
    user_id: str, *, timeout_seconds: float = 10.0, poll_interval: float = 0.5
) -> bool:
    """After SIGHUP, poll the user's MCP endpoint until it answers.

    Used by users lifecycle endpoints so the API reflects the reconcile result
    (not just "registry saved, eventually").
    """
    # Lazy import to avoid mcp_client → supervisor_hooks import cycles.
    from .mcp_client import probe_reachable

    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        if await probe_reachable(user_id):
            return True
        await asyncio.sleep(poll_interval)
    return False
