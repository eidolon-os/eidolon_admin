"""Per-realm memory runner and consolidator discovery.

memory-supervisor spawns ``eidolon-memory-agent`` and (opt-in)
``eidolon-memory-consolidator`` per enabled memory realm in Eidolon Data.
supervisord cannot see those grandchildren, so we surface them here by:

1. Reading `eidolon_data`'s memory_realms table.
2. Scanning processes via psutil for each CLI, binding ``--memory-space-id``.
3. TCP-probing each realm's agent port.
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psutil
from eidolon_data import load_settings

_AGENT_CLI = "eidolon-memory-agent"
_CONSOLIDATOR_CLI = "eidolon-memory-consolidator"

def realms_source_path() -> Path:
    return Path(load_settings().sqlite_path).expanduser().resolve()


def memory_log_dir() -> Path:
    return Path(
        os.environ.get("EIDOLON_MEMORY_LOG_DIR", Path.home() / "eidolon" / "logs" / "memory")
    ).expanduser()


def child_log_path(memory_realm_id: str, kind: str = "agent") -> str:
    """Log file path memory-supervisor uses (``{kind}_{memory_realm_id}.log``)."""
    return str(memory_log_dir() / f"{kind}_{memory_realm_id}.log")


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
class RealmEntry:
    memory_realm_id: str
    owner_id: str
    companion_id: str
    port: int
    enabled: bool
    engine: str = "mempalace"
    status: str = "active"
    palace_path: str = ""
    consolidator: ConsolidatorConfig | None = None

    @property
    def id(self) -> str:
        return self.memory_realm_id

    def consolidator_configured(self) -> bool:
        return self.consolidator is not None

    def consolidator_enabled(self) -> bool:
        return bool(self.consolidator and self.consolidator.enabled)


def load_realms() -> list[RealmEntry]:
    return _load_realms_from_eidolon_data(realms_source_path())


def _load_realms_from_eidolon_data(db_path: Path) -> list[RealmEntry]:
    if not db_path.is_file():
        return []
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error:
        return []
    try:
        rows = conn.execute(
            """
            SELECT
                r.realm_id,
                r.owner_id,
                r.companion_id,
                r.engine,
                r.engine_config_json,
                r.policy_json,
                r.status,
                o.profile_json AS owner_profile_json,
                o.settings_json AS owner_settings_json
            FROM memory_realms r
            JOIN owners o ON o.owner_id = r.owner_id
            ORDER BY r.created_at
            """
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()

    entries: list[RealmEntry] = []
    for row in rows:
        profile = _json_dict(row["owner_profile_json"])
        settings = _json_dict(row["owner_settings_json"])
        engine_config = _json_dict(row["engine_config_json"])
        registry = profile.get("registry") if isinstance(profile.get("registry"), dict) else {}
        enabled = bool(registry.get("enabled", True)) and str(row["status"] or "") == "active"
        consolidator = ConsolidatorConfig.from_yaml(settings.get("consolidator")) or ConsolidatorConfig()
        entries.append(
            RealmEntry(
                memory_realm_id=str(row["realm_id"]),
                owner_id=str(row["owner_id"]),
                companion_id=str(row["companion_id"]),
                port=int(engine_config.get("memory_port") or settings.get("memory_port") or 0),
                enabled=enabled,
                engine=str(row["engine"] or "mempalace"),
                status=str(row["status"] or "active"),
                palace_path=str(engine_config.get("palace_path") or settings.get("palace_path") or ""),
                consolidator=consolidator,
            )
        )
    return entries


def _json_dict(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if value in (None, ""):
        return {}
    try:
        data = json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _memory_realm_id_from_cmdline(cmd: list[str]) -> str | None:
    """Return the opaque ``--memory-space-id`` value from a worker CLI."""
    for i, token in enumerate(cmd):
        if token == "--memory-space-id" and i + 1 < len(cmd):
            return cmd[i + 1]
        if token.startswith("--memory-space-id="):
            return token.split("=", 1)[1]
    return None


def find_processes_by_cli(binary_name: str) -> dict[str, psutil.Process]:
    """Map memory_realm_id -> Process for every live subprocess matching ``binary_name``."""
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
        realm_id = _memory_realm_id_from_cmdline(cmd)
        if not realm_id:
            continue
        out[realm_id] = proc
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
    u: RealmEntry,
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
    source_path = realms_source_path()
    realms = load_realms()
    pid_map = find_agent_processes()
    cons_map = find_consolidator_processes()

    async def _one(u: RealmEntry) -> dict[str, Any]:
        proc = pid_map.get(u.id)
        listening = await _probe_tcp("127.0.0.1", u.port) if u.enabled else False
        meta = _proc_meta(proc)
        running = proc is not None
        return {
            "memory_realm_id": u.memory_realm_id,
            "owner_id": u.owner_id,
            "companion_id": u.companion_id,
            "port": u.port,
            "enabled": u.enabled,
            "engine": u.engine,
            "status": u.status,
            "palace_path": u.palace_path,
            "running": running,
            "listening": listening,
            "agent_log_path": child_log_path(u.id, "agent"),
            "consolidator": _consolidator_row(u, cons_map.get(u.id)),
            **meta,
        }

    results = await asyncio.gather(*(_one(u) for u in realms))

    known_ids = {u.id for u in realms}
    orphans = []
    for realm_id, proc in pid_map.items():
        if realm_id in known_ids:
            continue
        orphans.append({"memory_realm_id": realm_id, "role": "agent", **_proc_meta(proc)})

    cons_orphans = []
    for realm_id, proc in cons_map.items():
        if realm_id in known_ids:
            continue
        cons_orphans.append({"memory_realm_id": realm_id, "role": "consolidator", **_proc_meta(proc)})

    return {
        "realms_source": str(source_path),
        "realms_source_type": "eidolon_data",
        "realms_source_exists": source_path.exists(),
        "runners": results,
        "orphans": orphans,
        "consolidator_orphans": cons_orphans,
    }
