"""Resolve config file declarations from services.yaml into actionable entries.

Each service's ``configs:`` block lists files this admin can view/edit. We
resolve ``~`` and ``$VAR`` in paths so the user can write portable specs in
services.yaml while still pointing at real files on each machine.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ..settings import GatewayConfig, ServiceConfig


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
    return Path(os.path.expandvars(p)).expanduser().resolve()


def build_registry(cfg: GatewayConfig) -> list[ResolvedConfig]:
    out: list[ResolvedConfig] = []
    for svc in cfg.services:
        for entry in svc.configs:
            target = _resolve(str(entry.path))
            template = _resolve(str(entry.template)) if entry.template else None
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
