"""Per-user agent_runner discovery.

memory-supervisor spawns one ``eidolon-memory-agent`` subprocess per enabled
user in ``users.yaml``. supervisord can't see those grandchildren, so we
surface them here by:

1. Reading users.yaml directly (path: $EIDOLON_MEMORY_USERS_YAML, defaults to
   ~/eidolon/memory/config/users.yaml).
2. Scanning all running processes via psutil for ``eidolon-memory-agent``
   command lines, and binding each to its ``--user-id`` flag.
3. TCP-probing each user's port so the UI can tell "process alive but socket
   not bound" from "process gone".
"""
from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psutil
import yaml

_DEFAULT_USERS_YAML = Path.home() / "eidolon" / "memory" / "config" / "users.yaml"


def users_yaml_path() -> Path:
    return Path(os.environ.get("EIDOLON_MEMORY_USERS_YAML", _DEFAULT_USERS_YAML)).expanduser()


@dataclass
class UserEntry:
    id: str
    port: int
    enabled: bool
    palace_path: str = ""


def load_users(path: Path | None = None) -> list[UserEntry]:
    target = path or users_yaml_path()
    if not target.exists():
        return []
    data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    out: list[UserEntry] = []
    for u in data.get("users", []) or []:
        if not isinstance(u, dict) or not u.get("id"):
            continue
        out.append(
            UserEntry(
                id=str(u["id"]),
                port=int(u.get("port", 0) or 0),
                enabled=bool(u.get("enabled", True)),
                palace_path=str(u.get("palace_path", "") or ""),
            )
        )
    return out


def _user_id_from_cmdline(cmd: list[str]) -> str | None:
    for i, token in enumerate(cmd):
        if token == "--user-id" and i + 1 < len(cmd):
            return cmd[i + 1]
        if token.startswith("--user-id="):
            return token.split("=", 1)[1]
    return None


def find_agent_processes() -> dict[str, psutil.Process]:
    """Map user_id -> psutil.Process for every live agent_runner we can see."""
    out: dict[str, psutil.Process] = {}
    for proc in psutil.process_iter(attrs=["cmdline", "create_time"]):
        try:
            cmd = proc.info.get("cmdline") or []
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if not cmd:
            continue
        joined = " ".join(cmd)
        if "eidolon-memory-agent" not in joined:
            continue
        uid = _user_id_from_cmdline(cmd)
        if not uid:
            continue
        out[uid] = proc
    return out


async def _probe_tcp(host: str, port: int, timeout: float = 0.8) -> bool:
    if port <= 0:
        return False
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )
    except (OSError, asyncio.TimeoutError):
        return False
    try:
        writer.close()
        try:
            await writer.wait_closed()
        except (OSError, asyncio.CancelledError):
            pass
    finally:
        pass
    return True


def _proc_meta(proc: psutil.Process | None) -> dict[str, Any]:
    if proc is None:
        return {"pid": None, "uptime_sec": None, "cpu_percent": None, "rss_mb": None}
    try:
        with proc.oneshot():
            create = proc.create_time()
            try:
                rss = proc.memory_info().rss / (1024 * 1024)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                rss = None
            try:
                cpu = proc.cpu_percent(interval=None)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                cpu = None
            uptime = int(time.time() - create) if create else None
        return {
            "pid": proc.pid,
            "uptime_sec": uptime,
            "cpu_percent": cpu,
            "rss_mb": round(rss, 1) if rss is not None else None,
        }
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return {"pid": None, "uptime_sec": None, "cpu_percent": None, "rss_mb": None}


async def list_runners() -> dict[str, Any]:
    yaml_path = users_yaml_path()
    users = load_users(yaml_path)
    pid_map = find_agent_processes()

    async def _one(u: UserEntry) -> dict[str, Any]:
        proc = pid_map.get(u.id)
        listening = await _probe_tcp("127.0.0.1", u.port) if u.enabled else False
        meta = _proc_meta(proc)
        running = proc is not None
        return {
            "user_id": u.id,
            "port": u.port,
            "enabled": u.enabled,
            "palace_path": u.palace_path,
            "running": running,
            "listening": listening,
            **meta,
        }

    results = await asyncio.gather(*(_one(u) for u in users))

    # Surface stray agent processes that don't appear in users.yaml — usually
    # means yaml was edited but supervisor not yet SIGHUP'd.
    known_ids = {u.id for u in users}
    orphans = []
    for uid, proc in pid_map.items():
        if uid in known_ids:
            continue
        meta = _proc_meta(proc)
        orphans.append({"user_id": uid, **meta})

    return {
        "users_yaml": str(yaml_path),
        "users_yaml_exists": yaml_path.exists(),
        "runners": results,
        "orphans": orphans,
    }
