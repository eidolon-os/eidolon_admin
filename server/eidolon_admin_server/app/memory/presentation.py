"""Map registry users + live processes into API models."""
from __future__ import annotations

import time

from .runners import (
    UserEntry,
    child_log_path,
    find_agent_processes,
    find_consolidator_processes,
)
from .schemas import ConsolidatorStatus


def consolidator_status(
    entry: UserEntry,
    *,
    cons_map: dict | None = None,
) -> ConsolidatorStatus:
    proc_map = cons_map if cons_map is not None else find_consolidator_processes()
    proc = proc_map.get(entry.id)
    cfg = entry.consolidator
    uptime: int | None = None
    pid: int | None = None
    if proc is not None:
        try:
            pid = proc.pid
            uptime = int(time.time() - proc.create_time())
        except Exception:  # noqa: BLE001
            pass
    return ConsolidatorStatus(
        configured=entry.consolidator_configured(),
        enabled=entry.consolidator_enabled(),
        interval_hours=cfg.interval_hours if cfg else None,
        window_days=cfg.window_days if cfg else None,
        min_drawers=cfg.min_drawers if cfg else None,
        min_confidence=cfg.min_confidence if cfg else None,
        running=proc is not None,
        pid=pid,
        uptime_sec=uptime,
        log_path=child_log_path(entry.id, "consolidator"),
    )


def agent_log_path_for(entry: UserEntry) -> str:
    return child_log_path(entry.id, "agent")


def memory_runtime_state(
    entry: UserEntry,
    *,
    worker_running: bool,
    agent_reachable: bool,
    palace_initialized: bool,
) -> str:
    if not entry.enabled:
        return "disabled"
    if agent_reachable:
        return "running"
    if worker_running:
        return "starting"
    if not palace_initialized:
        return "initializing"
    return "stopped"
