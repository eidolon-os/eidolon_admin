"""Channel module — process-only integration.

Channel (the LiveKit voice worker) has no HTTP / NATS admin surface. We do
NOT modify the channel project. All we expose here is:

  GET /api/channel/config     parsed deploy/.livekit-channel.env (secrets masked)

Process status and logs are reached through the existing supervisor endpoints
(/api/supervisor/programs/channel:channel-worker, /api/supervisor/.../logs).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from ..common.dotenv_view import read_dotenv_view

router = APIRouter(prefix="/channel", tags=["channel"])

def _default_env_path() -> Path:
    from ..settings import default_eidolon_root

    return default_eidolon_root() / "eidolon_channel" / "config" / ".env"


def _env_path() -> Path:
    return Path(os.environ.get("EIDOLON_CHANNEL_ENV_FILE") or _default_env_path()).expanduser()


@router.get("/config")
def get_channel_config() -> dict:
    return read_dotenv_view(
        _env_path(),
        missing_hint="copy deploy/livekit-channel.env.template to config/.env",
    )


def _benchmark_runs_dir() -> Path:
    from ..settings import default_eidolon_root

    override = os.environ.get("EIDOLON_CHANNEL_BENCHMARK_RUNS_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return default_eidolon_root() / "eidolon_channel" / "benchmarks" / "runs"


def _safe_name(value: str, *, label: str) -> str:
    if not value or "/" in value or "\\" in value or value in {".", ".."}:
        raise HTTPException(status_code=400, detail=f"invalid {label}")
    return value


def _load_metrics(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise HTTPException(status_code=404, detail="metrics not found")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"invalid metrics json: {exc}") from exc


def _runner_dirs(run_dir: Path) -> list[Path]:
    if not run_dir.exists():
        return []
    return sorted(
        p for p in run_dir.iterdir()
        if p.is_dir() and (p / "metrics.json").exists()
    )


def _run_summary(run_dir: Path) -> dict[str, Any]:
    runners: list[dict[str, Any]] = []
    for runner_dir in _runner_dirs(run_dir):
        metrics = _load_metrics(runner_dir / "metrics.json")
        runners.append(
            {
                "runner": runner_dir.name,
                "run": metrics.get("run", {}),
                "summary": metrics.get("summary", {}),
                "report_html": (runner_dir / "report.html").exists(),
            }
        )
    stat = run_dir.stat()
    return {
        "run_id": run_dir.name,
        "path": str(run_dir),
        "modified_at": stat.st_mtime,
        "runners": runners,
        "dashboard_html": (run_dir / "dashboard.html").exists(),
        "dashboard_with_room_html": (run_dir / "dashboard-with-room.html").exists(),
    }


@router.get("/benchmarks/runs")
def list_benchmark_runs() -> dict[str, Any]:
    runs_dir = _benchmark_runs_dir()
    if not runs_dir.exists():
        return {"runs_dir": str(runs_dir), "runs": []}
    runs = [
        _run_summary(path)
        for path in sorted(
            (p for p in runs_dir.iterdir() if p.is_dir()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    ]
    return {"runs_dir": str(runs_dir), "runs": runs}


@router.get("/benchmarks/runs/{run_id}")
def get_benchmark_run(run_id: str) -> dict[str, Any]:
    run_id = _safe_name(run_id, label="run_id")
    run_dir = _benchmark_runs_dir() / run_id
    if not run_dir.exists():
        raise HTTPException(status_code=404, detail="benchmark run not found")
    payload = _run_summary(run_dir)
    payload["metrics"] = {
        runner_dir.name: _load_metrics(runner_dir / "metrics.json")
        for runner_dir in _runner_dirs(run_dir)
    }
    return payload


@router.get("/benchmarks/runs/{run_id}/{runner}/metrics")
def get_benchmark_runner_metrics(run_id: str, runner: str) -> dict[str, Any]:
    run_id = _safe_name(run_id, label="run_id")
    runner = _safe_name(runner, label="runner")
    return _load_metrics(_benchmark_runs_dir() / run_id / runner / "metrics.json")
