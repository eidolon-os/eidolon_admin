"""Resolve config file declarations from services.yaml into actionable entries.

Each service's ``configs:`` block lists files this admin can view/edit. We
resolve ``~`` and ``$VAR`` in paths so the user can write portable specs in
services.yaml while still pointing at real files on each machine.

Defense-in-depth: even though only paths declared in services.yaml are
reachable through the registry, we *also* assert each resolved path falls
under EIDOLON_ROOT (the monorepo). So a buggy or adversarial
services.yaml that points at ``/etc/passwd`` or
``~/.ssh/id_rsa`` is rejected at startup with a clear error, rather than
silently exposing arbitrary host files through the configs editor.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ..settings import GatewayConfig, ServiceConfig, default_eidolon_root


@dataclass
class ResolvedConfig:
    service_id: str
    config_id: str
    label: str
    path: Path             # resolved absolute path
    format: str
    reload: str
    reload_target: str | None
    template: Path | None
    exists: bool


def _resolve(p: str) -> Path:
    """Expand $VAR + ~ in a yaml-supplied path."""
    if "$EIDOLON_ROOT" in p and not os.environ.get("EIDOLON_ROOT"):
        os.environ.setdefault("EIDOLON_ROOT", str(default_eidolon_root()))
    return Path(os.path.expandvars(p)).expanduser().resolve()


def _assert_inside_root(
    path: Path, root: Path, *, service_id: str, config_id: str, field: str
) -> None:
    """Reject paths that escape the monorepo root.

    Uses ``Path.is_relative_to`` (Python 3.9+); if the path can't be made
    relative to ``root`` we raise ``ValueError`` rather than ``return False``
    so this is unmistakably a *startup* failure — the operator can fix
    services.yaml before admin serves any traffic.
    """
    try:
        if path.is_relative_to(root):
            return
    except ValueError:
        # On some platforms is_relative_to can raise instead of returning False
        # (different drives on Windows, weird symlinks). Treat as outside.
        pass
    raise ValueError(
        f"services.yaml: service '{service_id}' config '{config_id}' field "
        f"'{field}' resolved to {path}, which is outside the sanctioned root "
        f"{root}. This admin only exposes files inside EIDOLON_ROOT — point "
        f"the entry at a file under that tree, or set EIDOLON_ROOT explicitly "
        f"if the monorepo lives somewhere unusual."
    )


def build_registry(cfg: GatewayConfig) -> list[ResolvedConfig]:
    root = default_eidolon_root()
    out: list[ResolvedConfig] = []
    for svc in cfg.services:
        for entry in svc.configs:
            target = _resolve(str(entry.path))
            _assert_inside_root(target, root, service_id=svc.id, config_id=entry.id, field="path")
            template: Path | None = None
            if entry.template:
                template = _resolve(str(entry.template))
                _assert_inside_root(
                    template, root, service_id=svc.id, config_id=entry.id, field="template"
                )
            out.append(ResolvedConfig(
                service_id=svc.id,
                config_id=entry.id,
                label=entry.label or entry.id,
                path=target,
                format=entry.format,
                reload=entry.reload,
                reload_target=entry.reload_target,
                template=template,
                exists=target.exists(),
            ))
    return out


def find(cfg: GatewayConfig, service_id: str, config_id: str) -> ResolvedConfig | None:
    for entry in build_registry(cfg):
        if entry.service_id == service_id and entry.config_id == config_id:
            return entry
    return None


def by_service(cfg: GatewayConfig) -> dict[str, list[ResolvedConfig]]:
    """Group entries by service_id for the tree view."""
    out: dict[str, list[ResolvedConfig]] = {}
    for entry in build_registry(cfg):
        out.setdefault(entry.service_id, []).append(entry)
    return out
