"""OS-level process & port introspection — pure reads, no business logic.

This is the infrastructure layer for ``system_health``. It knows how to
answer two questions:

  1. Who is listening on port N?
  2. Is process P a descendant of process R?

Built on psutil so it works the same on macOS / Linux. No knowledge of
services.yaml, no knowledge of supervisord's bucket layout — those are
the orchestrator's concern one layer up.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import psutil

logger = logging.getLogger(__name__)


@dataclass
class ProcessSnapshot:
    """The subset of ``psutil.Process`` info system_health actually uses.

    Captured eagerly so callers see a consistent picture even if the
    process exits mid-audit (psutil raises NoSuchProcess on lazy
    accesses; this dataclass is post-snapshot so it's safe to pass
    around freely).
    """
    pid: int
    ppid: int
    command: str
    create_time: float


def find_port_listener(port: int) -> ProcessSnapshot | None:
    """Return the process LISTENing on TCP ``port``, or None.

    Iterates psutil's per-process connection list because ``net_connections``
    requires root on macOS. The per-process scan is slower (O(processes))
    but works without elevated privileges, which matters for the dev
    workflow.
    """
    for proc in psutil.process_iter(attrs=["pid", "ppid", "name", "cmdline", "create_time"]):
        try:
            conns = proc.net_connections(kind="tcp")
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            continue
        for conn in conns:
            if conn.status != psutil.CONN_LISTEN:
                continue
            if conn.laddr.port != port:
                continue
            info = proc.info
            return ProcessSnapshot(
                pid=info["pid"],
                ppid=info["ppid"] or 0,
                command=_compact_cmdline(info["cmdline"] or [info.get("name") or ""]),
                create_time=info.get("create_time") or 0.0,
            )
    return None


def ppid_chain(pid: int, max_depth: int = 20) -> list[int]:
    """Walk PPID upward, returning [pid, ppid, gpid, ...] until init.

    Used to answer "is X a descendant of Y" without recursing on
    children (children-of relationship is sparser to compute via
    descendants(), so for "is X under root R" we walk up from X
    instead — O(depth) vs O(tree size)).

    Bounded by ``max_depth`` so a pathological /proc state doesn't loop.
    """
    chain: list[int] = []
    current = pid
    for _ in range(max_depth):
        chain.append(current)
        if current <= 1:
            return chain
        try:
            current = psutil.Process(current).ppid()
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            return chain
    logger.warning("ppid_chain hit max_depth=%d starting from %d", max_depth, pid)
    return chain


def is_descendant_of(pid: int, root_pid: int) -> bool:
    """True if ``pid`` is anywhere under ``root_pid`` in the PPID tree.

    Cheap: walks up at most ``max_depth`` ancestors of ``pid`` and stops
    on first match. Avoids needing supervisord's child set.
    """
    if pid == root_pid:
        return True
    for ancestor in ppid_chain(pid):
        if ancestor == root_pid:
            return True
    return False


def process_age_seconds(pid: int) -> int:
    """How long process ``pid`` has been alive (rounded down to integer
    seconds). Returns 0 if the process has exited.

    Useful in the orphan list — the operator can spot "this has been
    around for 11 hours" vs. "this just started 30s ago" at a glance.
    """
    try:
        return int(time.time() - psutil.Process(pid).create_time())
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return 0


def get_process(pid: int) -> ProcessSnapshot | None:
    """Snapshot one process by pid; None if it doesn't exist."""
    try:
        p = psutil.Process(pid)
        cmdline = p.cmdline() or [p.name()]
        return ProcessSnapshot(
            pid=pid,
            ppid=p.ppid(),
            command=_compact_cmdline(cmdline),
            create_time=p.create_time(),
        )
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return None


def send_signal(pid: int, sig: int) -> tuple[bool, str | None]:
    """Send ``sig`` to ``pid``. Returns (ok, error_message_or_None).

    Wraps psutil to translate exceptions into the simple shape the
    orchestrator returns to operators. We don't kill -9 by default —
    callers explicitly pass signal.SIGTERM or SIGKILL.
    """
    try:
        psutil.Process(pid).send_signal(sig)
        return True, None
    except psutil.NoSuchProcess:
        return False, "process not found"
    except psutil.AccessDenied:
        return False, "access denied (process owned by different user?)"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


# ---- internals ------------------------------------------------------------


def _compact_cmdline(parts: list[str], max_len: int = 200) -> str:
    """Compact a process cmdline into a single string for display.

    Truncated past ``max_len`` to keep operator UIs from drowning in
    nodejs / uvicorn invocations that can run hundreds of chars.
    """
    joined = " ".join(p for p in parts if p)
    if len(joined) > max_len:
        return joined[: max_len - 1] + "…"
    return joined
