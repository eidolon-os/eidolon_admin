"""Port registry — aggregate sub-project bind ports for admin; export EIDOLON_* env.

Sub-project ``config/settings.yaml`` files are the **source of truth** for how
each service starts. This module never writes into those files.

The Ops-owned ``config/ports.yaml`` is the host topology index used by Admin,
health checks and the macOS executor. Refresh it with ``ports collect`` after
changing a component port (``eidolon-ops start`` does this automatically).
"""

from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _default_ops_root() -> Path:
    explicit = os.environ.get("EIDOLON_OPS_ROOT", "").strip()
    return (
        Path(explicit).expanduser().resolve()
        if explicit
        else (_REPO_ROOT.parent / "eidolon_ops").resolve()
    )


_DEFAULT_PORTS_FILE = _default_ops_root() / "config" / "ports.yaml"

# Hard cap on array indices in dotted paths (legacy helper for unit tests).
_MAX_LIST_INDEX = 64

_PORTS_HEADER = """\
# Eidolon dev stack — Ops-owned port registry for Admin and the host executor.
#
# Sub-project config/settings.yaml files are the source of truth for bind ports.
# eidolon-ops start runs ``python -m eidolon_admin_server.app.ports collect`` to
# refresh this file (aggregation only — child settings are never modified).
#
# Edit admin / client_web / nats.http_port here when needed. Data, Kernel and
# eidolond currently have deployment-contract ports because their application
# settings intentionally do not own ASGI binding.

"""


def ports_file() -> Path:
    explicit = os.environ.get("EIDOLON_PORTS_FILE", "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    return _DEFAULT_PORTS_FILE


def load_ports(path: Path | None = None) -> dict[str, Any]:
    target = path or ports_file()
    if not target.is_file():
        return {}
    raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{target} must be a mapping")
    return raw


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return raw if isinstance(raw, dict) else {}


def _port_from_url(url: str, *, default: int) -> int:
    text = (url or "").strip()
    if not text:
        return default
    if "://" not in text:
        text = f"tcp://{text}"
    parsed = urlparse(text)
    if parsed.port is not None:
        return int(parsed.port)
    return default


def _deep_get(mapping: dict[str, Any], *keys: str, default: Any = None) -> Any:
    cur: Any = mapping
    for key in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
    return default if cur is None else cur


def collect_ports_from_subprojects(root: Path | None = None) -> dict[str, Any]:
    """Build the port registry dict by reading sub-project settings (no writes)."""
    from .settings import default_eidolon_root

    root = root or default_eidolon_root()
    ports = load_ports()

    agent_y = _read_yaml(root / "eidolon_agent/config/settings.yaml")
    hub_y = _read_yaml(root / "eidolon_hub/config/settings.yaml")
    memory_y = _read_yaml(root / "eidolon_memory/config/settings.yaml")
    channel_y = _read_yaml(root / "eidolon_channel/config/settings.yaml")
    lk_y = _read_yaml(_default_ops_root() / "deploy/livekit/livekit.yaml")

    http = agent_y.get("http") if isinstance(agent_y.get("http"), dict) else {}
    grpc = agent_y.get("grpc") if isinstance(agent_y.get("grpc"), dict) else {}
    ports["agent"] = {
        "http": {
            "port": int(
                http.get(
                    "port", _deep_get(ports, "agent", "http", "port", default=8180)
                )
            ),
        },
        "admin": {
            "port": int(
                http.get(
                    "admin_port",
                    _deep_get(ports, "agent", "admin", "port", default=8081),
                )
            ),
        },
        "grpc": {
            "port": int(
                grpc.get(
                    "tcp_port", _deep_get(ports, "agent", "grpc", "port", default=45051)
                )
            ),
        },
    }

    nats_url = str((agent_y.get("nats") or {}).get("url") or "")
    ports["nats"] = {
        "port": _port_from_url(
            nats_url, default=int(_deep_get(ports, "nats", "port", default=4222))
        ),
        "http_port": int(_deep_get(ports, "nats", "http_port", default=8222)),
    }

    api = hub_y.get("api") if isinstance(hub_y.get("api"), dict) else {}
    lk_hub = hub_y.get("livekit") if isinstance(hub_y.get("livekit"), dict) else {}
    esp32 = hub_y.get("esp32") if isinstance(hub_y.get("esp32"), dict) else {}
    lk_default = int(_deep_get(ports, "livekit", "port", default=7880))
    lk_port = _port_from_url(str(lk_hub.get("api_url") or ""), default=lk_default)
    if esp32.get("livekit_port") is not None:
        lk_port = int(esp32["livekit_port"])

    ports["hub"] = {
        "api": {
            "host": str(
                api.get(
                    "host", _deep_get(ports, "hub", "api", "host", default="0.0.0.0")
                )
            ),
            "port": int(
                api.get("port", _deep_get(ports, "hub", "api", "port", default=8082))
            ),
        },
    }

    # These are deployment bindings, not application settings. They match the
    # exact endpoints published by Kernel's system-services manifests.
    ports["data"] = {
        "api": {
            "host": "127.0.0.1",
            "port": int(_deep_get(ports, "data", "api", "port", default=8084)),
        },
        "workspace_api": {
            "host": "127.0.0.1",
            "port": int(
                _deep_get(ports, "data", "workspace_api", "port", default=8085)
            ),
        },
    }
    ports["kernel"] = {
        "api": {
            "host": "127.0.0.1",
            "port": int(_deep_get(ports, "kernel", "api", "port", default=8083)),
        },
    }
    ports["eidolond"] = {
        "api": {
            "host": "127.0.0.1",
            "port": int(_deep_get(ports, "eidolond", "api", "port", default=8090)),
        },
    }

    disc = (
        memory_y.get("discovery_http")
        if isinstance(memory_y.get("discovery_http"), dict)
        else {}
    )
    mcp = memory_y.get("mcp_http") if isinstance(memory_y.get("mcp_http"), dict) else {}
    sup = (
        memory_y.get("supervisor")
        if isinstance(memory_y.get("supervisor"), dict)
        else {}
    )
    ports["memory"] = {
        "discovery": {
            "host": str(
                disc.get(
                    "host",
                    _deep_get(
                        ports, "memory", "discovery", "host", default="127.0.0.1"
                    ),
                )
            ),
            "port": int(
                disc.get(
                    "port",
                    _deep_get(ports, "memory", "discovery", "port", default=8020),
                )
            ),
        },
        "mcp": {
            "port": int(
                mcp.get(
                    "port", _deep_get(ports, "memory", "mcp", "port", default=10030)
                )
            ),
        },
        # Phase 29.B.2 — supervisor's embedded admin HTTP, used by admin
        # for user CRUD. Defaults align with memory's SupervisorConfig.
        "supervisor_http": {
            "host": str(
                sup.get(
                    "admin_http_host",
                    _deep_get(
                        ports, "memory", "supervisor_http", "host", default="127.0.0.1"
                    ),
                )
            ),
            "port": int(
                sup.get(
                    "admin_http_port",
                    _deep_get(ports, "memory", "supervisor_http", "port", default=8019),
                )
            ),
        },
    }

    core = channel_y.get("core") if isinstance(channel_y.get("core"), dict) else {}
    ports["channel"] = {
        "worker": {
            "port": int(
                core.get(
                    "port", _deep_get(ports, "channel", "worker", "port", default=8766)
                )
            ),
        },
    }

    rtc = lk_y.get("rtc") if isinstance(lk_y.get("rtc"), dict) else {}
    turn = lk_y.get("turn") if isinstance(lk_y.get("turn"), dict) else {}
    ports["livekit"] = {
        "port": int(lk_y.get("port", lk_port)),
        "turn_udp_port": int(
            turn.get(
                "udp_port", _deep_get(ports, "livekit", "turn_udp_port", default=3478)
            )
        ),
        "rtc_port_start": int(
            rtc.get(
                "port_range_start",
                _deep_get(ports, "livekit", "rtc_port_start", default=50000),
            )
        ),
        "rtc_port_end": int(
            rtc.get(
                "port_range_end",
                _deep_get(ports, "livekit", "rtc_port_end", default=60000),
            )
        ),
    }

    ports.setdefault(
        "admin",
        _deep_get(
            ports,
            "admin",
            default={"api": {"host": "127.0.0.1", "port": 9000}, "web": {"port": 9001}},
        ),
    )
    ports.setdefault(
        "client_web", _deep_get(ports, "client_web", default={"port": 3001})
    )

    return ports


def write_ports_registry(ports: dict[str, Any], path: Path | None = None) -> Path:
    target = path or ports_file()
    target.write_text(
        _PORTS_HEADER + yaml.safe_dump(ports, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return target


def collect_ports_registry(root: Path | None = None) -> Path:
    """Refresh ``config/ports.yaml`` from sub-project settings (aggregation only)."""
    return write_ports_registry(collect_ports_from_subprojects(root))


def _env(name: str, value: Any) -> None:
    """Set the env var to ``value`` IF nothing meaningful is already there."""
    current = os.environ.get(name, "")
    if not current.strip():
        os.environ[name] = str(value)


def apply_ports_to_environ(ports: dict[str, Any] | None = None) -> dict[str, str]:
    """Set EIDOLON_* env vars from ports.yaml (never override existing values)."""
    p = ports or load_ports()
    exported: dict[str, str] = {}

    def put(name: str, value: Any) -> None:
        text = str(value)
        exported[name] = text
        _env(name, text)

    admin = p["admin"]
    put("EIDOLON_ADMIN_API_HOST", admin["api"]["host"])
    put("EIDOLON_ADMIN_API_PORT", admin["api"]["port"])
    put(
        "EIDOLON_ADMIN_API_URL",
        f"http://{admin['api']['host']}:{admin['api']['port']}",
    )
    put("EIDOLON_ADMIN_WEB_PORT", admin["web"]["port"])

    hub = p["hub"]
    put("EIDOLON_HUB_API_HOST", hub["api"]["host"])
    put("EIDOLON_HUB_API_PORT", hub["api"]["port"])

    data = p["data"]
    put("EIDOLON_DATA_API_HOST", data["api"]["host"])
    put("EIDOLON_DATA_API_PORT", data["api"]["port"])
    put("EIDOLON_DATA_WORKSPACE_API_HOST", data["workspace_api"]["host"])
    put("EIDOLON_DATA_WORKSPACE_API_PORT", data["workspace_api"]["port"])

    kernel = p["kernel"]
    put("EIDOLON_KERNEL_API_HOST", kernel["api"]["host"])
    put("EIDOLON_KERNEL_API_PORT", kernel["api"]["port"])

    eidolond = p["eidolond"]
    put("EIDOLON_SYSTEM_API_HOST", eidolond["api"]["host"])
    put("EIDOLON_SYSTEM_API_PORT", eidolond["api"]["port"])

    agent = p["agent"]
    put("EIDOLON_AGENT_HTTP_PORT", agent["http"]["port"])
    put("EIDOLON_AGENT_ADMIN_PORT", agent["admin"]["port"])
    put("EIDOLON_AGENT_GRPC_PORT", agent["grpc"]["port"])

    memory = p["memory"]
    put("EIDOLON_MEMORY_DISCOVERY_HOST", memory["discovery"]["host"])
    put("EIDOLON_MEMORY_DISCOVERY_PORT", memory["discovery"]["port"])
    put("EIDOLON_MEMORY_MCP_PORT", memory["mcp"]["port"])
    # Memory supervisor's admin HTTP (29.B.2)
    put("EIDOLON_MEMORY_SUPERVISOR_HTTP_HOST", memory["supervisor_http"]["host"])
    put("EIDOLON_MEMORY_SUPERVISOR_HTTP_PORT", memory["supervisor_http"]["port"])

    channel = p["channel"]
    put("EIDOLON_CHANNEL_WORKER_PORT", channel["worker"]["port"])

    put("EIDOLON_CLIENT_WEB_PORT", p["client_web"]["port"])

    nats = p["nats"]
    put("EIDOLON_NATS_PORT", nats["port"])
    put("EIDOLON_NATS_HTTP_PORT", nats["http_port"])

    livekit = p["livekit"]
    put("EIDOLON_LIVEKIT_PORT", livekit["port"])
    put("EIDOLON_LIVEKIT_TURN_UDP_PORT", livekit["turn_udp_port"])
    put("EIDOLON_LIVEKIT_RTC_PORT_START", livekit["rtc_port_start"])
    put("EIDOLON_LIVEKIT_RTC_PORT_END", livekit["rtc_port_end"])

    # Phase 31.A: Mementos (Electron-vite dev). Optional — only present
    # in ports.yaml when the operator has the mementos checkout. We
    # treat missing keys as "feature off" rather than KeyError, so
    # admin still boots on machines without that side project.
    mementos = p.get("mementos")
    if mementos:
        sidecar = mementos.get("sidecar", {})
        if "port" in sidecar:
            put("EIDOLON_MEMENTOS_PORT", sidecar["port"])
        if "host" in sidecar:
            put("EIDOLON_MEMENTOS_HOST", sidecar["host"])
        # Source directory — operators override this if their checkout
        # lives elsewhere. supervisord conf reads it as
        # %(ENV_EIDOLON_MEMENTOS_DIR)s for the program's working dir.
        if mementos.get("source_dir"):
            put("EIDOLON_MEMENTOS_DIR", mementos["source_dir"])

    return exported


def export_shell(ports: dict[str, Any] | None = None) -> str:
    """Print bash ``export`` statements for eval/source."""
    mapping = apply_ports_to_environ(ports)
    lines = [f"export {key}={shlex.quote(val)}" for key, val in sorted(mapping.items())]
    return "\n".join(lines)


def _set_nested(data: dict[str, Any], dotted: str, value: Any) -> bool:
    """Set ``dotted`` key (e.g. ``llm.models.0.api_base``) without clobbering lists."""
    parts = dotted.split(".")
    if not parts:
        return False
    cur: Any = data
    for i, part in enumerate(parts[:-1]):
        next_part = parts[i + 1]
        if part.isdigit():
            idx = int(part)
            if idx > _MAX_LIST_INDEX:
                return False
            if not isinstance(cur, list):
                return False
            while len(cur) <= idx:
                cur.append({})
            cur = cur[idx]
            continue
        if not isinstance(cur, dict):
            return False
        nxt = cur.get(part)
        if next_part.isdigit():
            if not isinstance(nxt, list):
                cur[part] = []
                nxt = cur[part]
        else:
            if not isinstance(nxt, dict):
                if nxt is None:
                    cur[part] = {}
                    nxt = cur[part]
                else:
                    return False
        cur = nxt
    leaf = parts[-1]
    if leaf.isdigit() or not isinstance(cur, dict):
        return False
    if cur.get(leaf) == value:
        return False
    cur[leaf] = value
    return True


def _sync_yaml(path: Path, updates: dict[str, Any]) -> bool:
    """Test helper — mirrors historical dotted-path yaml update (not used in prod)."""
    if not path.is_file():
        return False
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        return False
    changed = False
    for dotted, value in updates.items():
        if _set_nested(data, dotted, value):
            changed = True
    if changed:
        path.write_text(
            yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
    return changed


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args or args[0] not in {"export", "collect"}:
        print(
            "usage: python -m eidolon_admin_server.app.ports export|collect",
            file=sys.stderr,
        )
        return 2
    cmd = args[0]
    if cmd == "export":
        print(export_shell())
        return 0
    path = collect_ports_registry()
    print(f"collected {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
