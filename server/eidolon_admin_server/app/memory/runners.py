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
import yaml
from eidolon_data import load_settings
from eidolon_memory_contracts import memory_space_storage_name

from ..settings import default_eidolon_root
from .runtime_route import MemoryRuntimeRoute, default_mcp_base_port, route_for_realm

_AGENT_CLI = "eidolon-memory-agent"
_CONSOLIDATOR_CLI = "eidolon-memory-consolidator"
_BACKEND_ARTIFACTS = {
    "chroma": "chroma.sqlite3",
    "qdrant": "qdrant_backend.json",
    "pgvector": "pgvector_backend.json",
    "sqlite_exact": "sqlite_exact.sqlite3",
}
_SQLITE_REQUIRED_TABLES = {
    "chroma": frozenset({"collections", "embeddings"}),
    "sqlite_exact": frozenset({"collections", "documents"}),
}


def realms_source_path() -> Path:
    return Path(load_settings().sqlite_path).expanduser().resolve()


def memory_log_dir() -> Path:
    return Path(
        os.environ.get(
            "EIDOLON_MEMORY_LOG_DIR", Path.home() / "eidolon" / "logs" / "memory"
        )
    ).expanduser()


def memory_settings_path() -> Path:
    """Return the one Memory settings file used by the runtime and Admin."""
    configured = os.environ.get("EIDOLON_MEMORY_SETTINGS_YAML", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (default_eidolon_root() / "eidolon_memory" / "config" / "settings.yaml").resolve()


def _memory_settings_yaml() -> dict[str, Any]:
    path = memory_settings_path()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return raw if isinstance(raw, dict) else {}


def configured_mempalace_backend() -> str:
    """Read the globally authoritative MemPalace backend selection."""
    raw = _memory_settings_yaml().get("mempalace")
    if not isinstance(raw, dict):
        return "unknown"
    backend = str(raw.get("backend") or "").strip().lower()
    return backend or "unknown"


def memory_palaces_root() -> Path:
    """Resolve the same Palace root precedence used by the Memory runtime."""
    env = os.environ.get("EIDOLON_MEMORY_PALACES_ROOT", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    runtime = _memory_settings_yaml().get("runtime")
    configured = (
        str(runtime.get("palaces_root") or "").strip()
        if isinstance(runtime, dict)
        else ""
    )
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / "eidolon" / "memory" / "mempalaces").resolve()


def memory_palace_path(memory_realm_id: str) -> str:
    return str(memory_palaces_root() / memory_space_storage_name(memory_realm_id))


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
    runtime_route: MemoryRuntimeRoute
    enabled: bool
    engine: str = "mempalace"
    configured_backend: str = "chroma"
    status: str = "active"
    palace_path: str = ""
    consolidator: ConsolidatorConfig | None = None

    @property
    def port(self) -> int:
        return self.runtime_route.mcp_port

    @property
    def mcp_http_url(self) -> str:
        return self.runtime_route.mcp_http_url

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
    used_ports: set[int] = set()
    base_port = default_mcp_base_port()
    global_backend = configured_mempalace_backend()
    for row in rows:
        profile = _json_dict(row["owner_profile_json"])
        settings = _json_dict(row["owner_settings_json"])
        registry = (
            profile.get("registry") if isinstance(profile.get("registry"), dict) else {}
        )
        enabled = (
            bool(registry.get("enabled", True)) and str(row["status"] or "") == "active"
        )
        consolidator = (
            ConsolidatorConfig.from_yaml(settings.get("consolidator"))
            or ConsolidatorConfig()
        )
        memory_realm_id = str(row["realm_id"])
        entries.append(
            RealmEntry(
                memory_realm_id=memory_realm_id,
                owner_id=str(row["owner_id"]),
                companion_id=str(row["companion_id"]),
                runtime_route=route_for_realm(
                    memory_realm_id,
                    base_port=base_port,
                    used_ports=used_ports,
                ),
                enabled=enabled,
                engine=str(row["engine"] or "mempalace"),
                configured_backend=global_backend,
                status=str(row["status"] or "active"),
                palace_path=memory_palace_path(memory_realm_id),
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


def inspect_palace_backend(
    palace_path: str | Path,
    *,
    configured_backend: str = "chroma",
) -> dict[str, Any]:
    """Inspect backend artifacts without creating or mutating database files.

    Configuration remains authoritative. Artifact discovery is only a
    consistency check: an empty/corrupt foreign file is reported as invalid,
    while a second valid backend is reported as a conflict.
    """
    palace = Path(palace_path).expanduser()
    artifacts: list[dict[str, Any]] = []
    for backend, filename in _BACKEND_ARTIFACTS.items():
        path = palace / filename
        item: dict[str, Any] = {
            "backend": backend,
            "path": str(path),
            "state": "absent",
            "size_bytes": 0,
            "detail": "",
        }
        if path.is_file():
            try:
                item["size_bytes"] = path.stat().st_size
                if item["size_bytes"] == 0:
                    item["state"] = "invalid"
                    item["detail"] = "empty artifact"
                elif backend in _SQLITE_REQUIRED_TABLES:
                    item["state"], item["detail"] = _inspect_sqlite_artifact(
                        path,
                        _SQLITE_REQUIRED_TABLES[backend],
                    )
                else:
                    item["state"], item["detail"] = _inspect_json_artifact(path)
            except OSError as exc:
                item["state"] = "invalid"
                item["detail"] = f"{type(exc).__name__}: {exc}"
        artifacts.append(item)

    configured = configured_backend.strip().lower() or "chroma"
    selected = next((a for a in artifacts if a["backend"] == configured), None)
    foreign_valid = [
        a["backend"]
        for a in artifacts
        if a["backend"] != configured and a["state"] == "valid"
    ]
    invalid = [a for a in artifacts if a["state"] == "invalid"]

    if selected is None:
        state = "invalid"
        issue = f"unsupported configured backend {configured!r}"
    elif foreign_valid:
        state = "conflict"
        issue = (
            f"configured backend {configured!r} conflicts with valid artifacts: "
            f"{', '.join(foreign_valid)}"
        )
    elif selected["state"] == "invalid":
        state = "invalid"
        issue = f"configured backend {configured!r} artifact is invalid: {selected['detail']}"
    elif invalid:
        state = "stale_artifact"
        issue = "; ".join(
            f"{a['backend']}: {a['detail']}" for a in invalid
        )
    elif selected["state"] == "valid":
        state = "ready"
        issue = ""
    else:
        state = "uninitialized"
        issue = f"configured backend {configured!r} has no initialized artifact"

    return {
        "configured_backend": configured,
        "backend_state": state,
        "backend_issue": issue,
        "backend_artifacts": artifacts,
    }


def _inspect_sqlite_artifact(
    path: Path,
    required_tables: frozenset[str],
) -> tuple[str, str]:
    try:
        conn = sqlite3.connect(
            f"file:{path}?mode=ro&immutable=1",
            uri=True,
            timeout=2.0,
        )
        try:
            row = conn.execute("PRAGMA quick_check").fetchone()
            if not row or str(row[0]).strip().lower() != "ok":
                return "invalid", f"quick_check={row[0] if row else 'no result'}"
            tables = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return "invalid", f"{type(exc).__name__}: {exc}"
    missing = sorted(required_tables - tables)
    if missing:
        return "invalid", f"missing required tables: {', '.join(missing)}"
    return "valid", ""


def _inspect_json_artifact(path: Path) -> tuple[str, str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return "invalid", f"{type(exc).__name__}: {exc}"
    if not isinstance(payload, dict):
        return "invalid", "marker must contain a JSON object"
    return "valid", ""


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
            "configured_backend": u.configured_backend,
            "status": u.status,
            "palace_path": u.palace_path,
            **inspect_palace_backend(
                u.palace_path,
                configured_backend=u.configured_backend,
            ),
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
        orphans.append(
            {"memory_realm_id": realm_id, "role": "agent", **_proc_meta(proc)}
        )

    cons_orphans = []
    for realm_id, proc in cons_map.items():
        if realm_id in known_ids:
            continue
        cons_orphans.append(
            {"memory_realm_id": realm_id, "role": "consolidator", **_proc_meta(proc)}
        )

    return {
        "realms_source": str(source_path),
        "realms_source_type": "eidolon_data",
        "realms_source_exists": source_path.exists(),
        "runners": results,
        "orphans": orphans,
        "consolidator_orphans": cons_orphans,
    }
