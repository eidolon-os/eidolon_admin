"""Benchmark project registry and default filesystem roots."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ..settings import default_eidolon_root


@dataclass(frozen=True)
class ProjectDecl:
    id: str
    label: str


PROJECTS: tuple[ProjectDecl, ...] = (
    ProjectDecl("agent", "Eidolon Agent"),
    ProjectDecl("channel", "Eidolon Channel"),
    ProjectDecl("memory", "Eidolon Memory"),
    ProjectDecl("admin", "Eidolon Admin"),
    ProjectDecl("hub", "Eidolon Hub"),
    ProjectDecl("client-web", "Client Web"),
)


PROJECT_LABELS = {project.id: project.label for project in PROJECTS}


def env_path(name: str, default: Path) -> Path:
    value = os.environ.get(name, "").strip()
    if value:
        return Path(value).expanduser().resolve()
    return default.expanduser().resolve()


def agent_runs_dir() -> Path:
    return env_path(
        "EIDOLON_AGENT_BENCHMARK_RUNS_DIR",
        default_eidolon_root() / "eidolon_agent" / "benchmarks" / "runs",
    )


def agent_debug_reports_dir() -> Path:
    return env_path(
        "EIDOLON_AGENT_REPORTS_DIR",
        Path.home() / "eidolon" / "debug" / "reports",
    )


def channel_runs_dir() -> Path:
    return env_path(
        "EIDOLON_CHANNEL_BENCHMARK_RUNS_DIR",
        default_eidolon_root() / "eidolon_channel" / "benchmarks" / "runs",
    )


def memory_reports_dir() -> Path:
    return env_path(
        "EIDOLON_MEMORY_BENCHMARK_REPORTS_DIR",
        default_eidolon_root() / "eidolon_memory" / "reports",
    )
