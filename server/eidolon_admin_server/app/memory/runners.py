"""Per-user agent_runner and consolidator discovery.

memory-supervisor spawns ``eidolon-memory-agent`` and (opt-in)
``eidolon-memory-consolidator`` per enabled user in ``users.yaml``. supervisord
cannot see those grandchildren, so we surface them here by:

1. Reading users.yaml (``$EIDOLON_MEMORY_USERS_YAML``).
2. Scanning processes via psutil for each CLI, binding ``--user-id``.
3. TCP-probing each user's agent port.
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

_AGENT_CLI = "eidolon-memory-agent"
_CONSOLIDATOR_CLI = "eidolon-memory-consolidator"
_ADMIN_REPO_ROOT = Path(__file__).resolve().parents[3]


def _default_users_yaml() -> Path:
    """``eidolon_memory/config/users.yaml`` under the monorepo root."""
    root = os.environ.get("EIDOLON_ROOT", "").strip()
    base = Path(root).expanduser() if root else _ADMIN_REPO_ROOT.parent
    return (base / "eidolon_memory" / "config" / "users.yaml").resolve()


def users_yaml_path() -> Path:
    raw = os.environ.get("EIDOLON_MEMORY_USERS_YAML", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return _default_users_yaml()


def memory_log_dir() -> Path:
    return Path(
        os.environ.get("EIDOLON_MEMORY_LOG_DIR", Path.home() / "eidolon" / "logs" / "memory")
    ).expanduser()


def child_log_path(user_id: str, kind: str = "agent") -> str:
    """Log file path memory-supervisor uses (``{kind}_{user_id}.log``)."""
    return str(memory_log_dir() / f"{kind}_{user_id}.log")


@dataclass
class ConsolidatorConfig:
    enabled: bool = False
    interval_hours: float = 6.0
    window_days: int = 30
    min_drawers: int = 3
    min_confidence: float = 0.6

    @classmethod
    def from_yaml(cls, raw: object) -> ConsolidatorConfig | None:
        if raw is None:
            return None
        if not isinstance(raw, dict):
            return None
        return cls(
            enabled=bool(raw.get("enabled", False)),
            interval_hours=float(raw.get("interval_hours", 6.0) or 6.0),
            window_days=int(raw.get("window_days", 30) or 30),
            min_drawers=int(raw.get("min_drawers", 3) or 3),
            min_confidence=float(raw.get("min_confidence", 0.6) or 0.6),
        )

    def to_yaml_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "interval_hours": self.interval_hours,
            "window_days": self.window_days,
            "min_drawers": self.min_drawers,
            "min_confidence": self.min_confidence,
        }

    def consolidator_enabled(self) -> bool:
        return self.enabled


@dataclass
class UserEntry:
    id: str
    port: int
    enabled: bool
    palace_path: str = ""
    consolidator: ConsolidatorConfig | None = None

    def consolidator_configured(self) -> bool:
        return self.consolidator is not None

    def consolidator_enabled(self) -> bool:
        return bool(self.consolidator and self.consolidator.enabled)


def parse_user_dict(u: dict[str, Any]) -> UserEntry | None:
    uid = u.get("id")
    if not uid:
        return None
    return UserEntry(
        id=str(uid),
        port=int(u.get("port", 0) or 0),
        enabled=bool(u.get("enabled", True)),
        palace_path=str(u.get("palace_path", "") or ""),
        consolidator=ConsolidatorConfig.from_yaml(u.get("consolidator")),
    )


def user_entry_to_dict(u: UserEntry) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": u.id,
        "port": u.port,
        "enabled": u.enabled,
        "palace_path": u.palace_path,
    }
    if u.consolidator is not None:
        row["consolidator"] = u.consolidator.to_yaml_dict()
    return row


def load_users(path: Path | None = None) -> list[UserEntry]:
    target = path or users_yaml_path()
    if not target.exists():
        return []
    data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    out: list[UserEntry] = []
    for u in data.get("users", []) or []:
        if not isinstance(u, dict):
            continue
        entry = parse_user_dict(u)
        if entry:
            out.append(entry)
    return out


def serialize_users(users: list[UserEntry]) -> str:
    data = {"users": [user_entry_to_dict(u) for u in users]}
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def _user_id_from_cmdline(cmd: list[str]) -> str | None:
    for i, token in enumerate(cmd):
        if token == "--user-id" and i + 1 < len(cmd):
            return cmd[i + 1]
        if token.startswith("--user-id="):
            return token.split("=", 1)[1]
    return None


def find_processes_by_cli(binary_name: str) -> dict[str, psutil.Process]:
    """Map user_id -> Process for every live subprocess matching ``binary_name``."""
    out: dict[str, psutil.Process] = {}
    for proc in psutil.process_iter(attrs=["cmdline", "create_time"]):
        try:
            cmd = proc.info.get("cmdline") or []
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if not cmd:
            continue
        if binary_name not in " ".join(cmd):
            continue
        uid = _user_id_from_cmdline(cmd)
        if not uid:
            continue
        out[uid] = proc
    return out


def find_agent_processes() -> dict[str, psutil.Process]:
    return find_processes_by_cli(_AGENT_CLI)


def find_consolidator_processes() -> dict[str, psutil.Process]:
    return find_processes_by_cli(_CONSOLIDATOR_CLI)


async def _probe_tcp(host: str, port: int, timeout: float = 0.8) -> bool:
    if port <= 0:
        return False
    try:
        _reader, writer = await asyncio.wait_for(
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


def _consolidator_row(
    u: UserEntry,
    proc: psutil.Process | None,
) -> dict[str, Any]:
    meta = _proc_meta(proc)
    cfg = u.consolidator
    return {
        "configured": u.consolidator_configured(),
        "enabled": u.consolidator_enabled(),
        "interval_hours": cfg.interval_hours if cfg else None,
        "window_days": cfg.window_days if cfg else None,
        "min_drawers": cfg.min_drawers if cfg else None,
        "min_confidence": cfg.min_confidence if cfg else None,
        "running": proc is not None,
        "log_path": child_log_path(u.id, "consolidator"),
        **meta,
    }


async def list_runners() -> dict[str, Any]:
    yaml_path = users_yaml_path()
    users = load_users(yaml_path)
    pid_map = find_agent_processes()
    cons_map = find_consolidator_processes()

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
            "agent_log_path": child_log_path(u.id, "agent"),
            "consolidator": _consolidator_row(u, cons_map.get(u.id)),
            **meta,
        }

    results = await asyncio.gather(*(_one(u) for u in users))

    known_ids = {u.id for u in users}
    orphans = []
    for uid, proc in pid_map.items():
        if uid in known_ids:
            continue
        orphans.append({"user_id": uid, "role": "agent", **_proc_meta(proc)})

    cons_orphans = []
    for uid, proc in cons_map.items():
        if uid in known_ids:
            continue
        cons_orphans.append({"user_id": uid, "role": "consolidator", **_proc_meta(proc)})

    return {
        "users_yaml": str(yaml_path),
        "users_yaml_exists": yaml_path.exists(),
        "runners": results,
        "orphans": orphans,
        "consolidator_orphans": cons_orphans,
    }
