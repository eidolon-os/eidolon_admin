"""Port registry — load config/ports.yaml, export env vars, sync sub-project configs."""
from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_PORTS_FILE = _REPO_ROOT / "config" / "ports.yaml"


def ports_file() -> Path:
    explicit = os.environ.get("EIDOLON_PORTS_FILE", "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    return _DEFAULT_PORTS_FILE


def load_ports(path: Path | None = None) -> dict[str, Any]:
    target = path or ports_file()
    raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{target} must be a mapping")
    return raw


def _env(name: str, value: Any) -> None:
    if name not in os.environ:
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
    put("EIDOLON_ADMIN_WEB_PORT", admin["web"]["port"])

    hub = p["hub"]
    put("EIDOLON_HUB_API_HOST", hub["api"]["host"])
    put("EIDOLON_HUB_API_PORT", hub["api"]["port"])

    agent = p["agent"]
    put("EIDOLON_AGENT_HTTP_PORT", agent["http"]["port"])
    put("EIDOLON_AGENT_ADMIN_PORT", agent["admin"]["port"])
    put("EIDOLON_AGENT_GRPC_PORT", agent["grpc"]["port"])

    memory = p["memory"]
    put("EIDOLON_MEMORY_DISCOVERY_HOST", memory["discovery"]["host"])
    put("EIDOLON_MEMORY_DISCOVERY_PORT", memory["discovery"]["port"])
    put("EIDOLON_MEMORY_MCP_PORT", memory["mcp"]["port"])

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

    return exported


def export_shell(ports: dict[str, Any] | None = None) -> str:
    """Print bash ``export`` statements for eval/source."""
    mapping = apply_ports_to_environ(ports)
    lines = [f"export {key}={shlex.quote(val)}" for key, val in sorted(mapping.items())]
    return "\n".join(lines)


def _set_nested(data: dict[str, Any], dotted: str, value: Any) -> bool:
    parts = dotted.split(".")
    cur: Any = data
    for part in parts[:-1]:
        if part.isdigit():
            idx = int(part)
            if not isinstance(cur, list):
                return False
            while len(cur) <= idx:
                cur.append({})
            cur = cur[idx]
            continue
        nxt = cur.get(part) if isinstance(cur, dict) else None
        if not isinstance(nxt, dict):
            if not isinstance(cur, dict):
                return False
            cur[part] = {}
            nxt = cur[part]
        cur = nxt
    leaf = parts[-1]
    if not isinstance(cur, dict):
        return False
    if cur.get(leaf) == value:
        return False
    cur[leaf] = value
    return True


def _sync_yaml(path: Path, updates: dict[str, Any]) -> bool:
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
        path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return changed


def _sync_dotenv_key(path: Path, key: str, value: str) -> bool:
    if not path.is_file():
        return False
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    prefix = f"{key}="
    replaced = False
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            out.append(line)
            continue
        name = stripped.split("=", 1)[0].strip()
        if name == key:
            out.append(f"{key}={value}\n")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        if out and not out[-1].endswith("\n"):
            out[-1] = out[-1] + "\n"
        out.append(f"{key}={value}\n")
        replaced = True
    if replaced:
        path.write_text("".join(out), encoding="utf-8")
    return replaced


def sync_subproject_configs(ports: dict[str, Any] | None = None) -> list[str]:
    """Push port registry into sub-project settings the stack reads at runtime."""
    from .settings import default_eidolon_root

    p = ports or load_ports()
    root = default_eidolon_root()
    lk_port = p["livekit"]["port"]
    nats_url = f"nats://127.0.0.1:{p['nats']['port']}"
    agent_http = p["agent"]["http"]["port"]
    discovery_port = p["memory"]["discovery"]["port"]
    changed: list[str] = []

    targets: list[tuple[Path, dict[str, Any]]] = [
        (
            root / "eidolon_hub/config/settings.yaml",
            {
                "api.host": p["hub"]["api"]["host"],
                "api.port": p["hub"]["api"]["port"],
                "livekit.api_url": f"http://127.0.0.1:{lk_port}",
                "esp32.livekit_port": lk_port,
            },
        ),
        (
            root / "eidolon_agent/config/settings.yaml",
            {
                "grpc.tcp_port": p["agent"]["grpc"]["port"],
                "http.port": agent_http,
                "http.admin_port": p["agent"]["admin"]["port"],
                "nats.url": nats_url,
                "memory.discovery_url": (
                    f"http://127.0.0.1:{discovery_port}/api/discovery/agent-routing"
                ),
                "llm.models.0.api_base": f"http://127.0.0.1:{agent_http}/v1",
            },
        ),
        (
            root / "eidolon_memory/config/settings.yaml",
            {
                "discovery_http.host": p["memory"]["discovery"]["host"],
                "discovery_http.port": discovery_port,
                "mcp_http.port": p["memory"]["mcp"]["port"],
                "nats.url": nats_url,
                "llm.base_url": f"http://127.0.0.1:{agent_http}/v1",
            },
        ),
        (
            root / "eidolon_channel/config/settings.yaml",
            {
                "core.livekit_url": f"ws://127.0.0.1:{lk_port}",
                "core.port": p["channel"]["worker"]["port"],
            },
        ),
        (
            _REPO_ROOT / "deploy/livekit/livekit.yaml",
            {
                "port": lk_port,
                "turn.udp_port": p["livekit"]["turn_udp_port"],
                "rtc.port_range_start": p["livekit"]["rtc_port_start"],
                "rtc.port_range_end": p["livekit"]["rtc_port_end"],
            },
        ),
    ]

    for path, updates in targets:
        if _sync_yaml(path, updates):
            changed.append(str(path))

    channel_env = root / "eidolon_channel/config/.env"
    if _sync_dotenv_key(channel_env, "LIVEKIT_URL", f"ws://127.0.0.1:{lk_port}"):
        changed.append(str(channel_env))

    return changed


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args or args[0] not in {"export", "sync"}:
        print("usage: python -m eidolon_admin_server.app.ports export|sync", file=sys.stderr)
        return 2
    cmd = args[0]
    if cmd == "export":
        print(export_shell())
        return 0
    changed = sync_subproject_configs()
    for path in changed:
        print(f"synced {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
