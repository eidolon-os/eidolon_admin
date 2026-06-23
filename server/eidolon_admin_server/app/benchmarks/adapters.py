"""Filesystem adapters that normalize existing benchmark artifacts."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from . import registry
from .schemas import BenchmarkArtifact, BenchmarkRunDetail, BenchmarkRunSummary, BenchmarkStatus
from .trash import TrashError, move_file_group_to_trash, move_to_trash

KNOWN_EMPTY_SUITES: dict[str, tuple[tuple[str, str], ...]] = {
    "admin": (("smoke", "Smoke"),),
    "hub": (("smoke", "Smoke"),),
    "client-web": (("web", "Web"),),
}


@dataclass
class RunRecord:
    summary: BenchmarkRunSummary
    payload: dict[str, Any] = field(default_factory=dict)
    cases: list[dict[str, Any]] = field(default_factory=list)
    markdown: str | None = None
    source_path: Path | None = None
    source_paths: list[Path] = field(default_factory=list)
    root: Path | None = None


def list_runs(project: str | None = None, suite: str | None = None) -> list[BenchmarkRunSummary]:
    records = _list_records()
    summaries = [record.summary for record in records]
    if project:
        summaries = [run for run in summaries if run.project == project]
    if suite:
        summaries = [run for run in summaries if run.suite == suite]
    summaries.sort(key=lambda run: run.modified_at, reverse=True)
    return summaries


def get_run(project: str, suite: str, run_id: str) -> BenchmarkRunDetail:
    record = _find_record(project, suite, run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="benchmark run not found")
    return BenchmarkRunDetail(
        **record.summary.model_dump(),
        cases=record.cases,
        payload=record.payload,
        markdown=record.markdown,
    )


def delete_run(project: str, suite: str, run_id: str) -> Path:
    record = _find_record(project, suite, run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="benchmark run not found")
    if not record.summary.deletable or (
        record.source_path is None and not record.source_paths
    ) or record.root is None:
        raise HTTPException(
            status_code=409,
            detail=record.summary.delete_hint or "benchmark run is not deletable",
        )
    try:
        if record.source_paths:
            return move_file_group_to_trash(
                sources=record.source_paths,
                root=record.root,
                project=project,
                suite=suite,
                run_id=run_id,
            )
        return move_to_trash(
            source=record.source_path,
            root=record.root,
            project=project,
            suite=suite,
            run_id=run_id,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="benchmark run not found") from exc
    except TrashError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def known_suites() -> dict[str, dict[str, str]]:
    suites: dict[str, dict[str, str]] = {
        "agent": {
            "realtime": "Realtime",
            "replay": "Replay",
        },
        "channel": {
            "voice": "Voice",
        },
        "memory": {
            "memory_perf": "Memory Perf",
            "memory_quality": "Memory Quality",
        },
    }
    for project, entries in KNOWN_EMPTY_SUITES.items():
        suites[project] = {suite_id: label for suite_id, label in entries}
    for run in list_runs():
        suites.setdefault(run.project, {})[run.suite] = run.suite_label
    return suites


def _list_records() -> list[RunRecord]:
    records: list[RunRecord] = []
    records.extend(_agent_project_records())
    records.extend(_agent_debug_records())
    records.extend(_channel_records())
    records.extend(_memory_records())
    return records


def _find_record(project: str, suite: str, run_id: str) -> RunRecord | None:
    _validate_segment(project, "project")
    _validate_segment(suite, "suite")
    _validate_segment(run_id, "run_id")
    for record in _list_records():
        summary = record.summary
        if summary.project == project and summary.suite == suite and summary.run_id == run_id:
            return record
    return None


def _agent_project_records() -> list[RunRecord]:
    root = registry.agent_runs_dir()
    if not root.exists():
        return []
    records: list[RunRecord] = []
    for suite_dir in _child_dirs(root):
        suite = suite_dir.name
        label = _title(suite)
        for item in sorted(suite_dir.iterdir(), key=_mtime, reverse=True):
            if item.name.startswith("."):
                continue
            if item.is_dir():
                record = _directory_record(
                    project="agent",
                    project_label=registry.PROJECT_LABELS["agent"],
                    suite=suite,
                    suite_label=label,
                    run_id=item.name,
                    run_dir=item,
                    root=root,
                    deletable=True,
                )
                if record:
                    records.append(record)
            elif item.suffix == ".json":
                record = _json_file_record(
                    project="agent",
                    project_label=registry.PROJECT_LABELS["agent"],
                    suite=suite,
                    suite_label=label,
                    path=item,
                    root=root,
                    deletable=True,
                    delete_hint=None,
                )
                if record:
                    records.append(record)
    return records


def _agent_debug_records() -> list[RunRecord]:
    root = registry.agent_debug_reports_dir()
    records: list[RunRecord] = []
    for suite in ("realtime", "replay"):
        suite_dir = root / suite
        if not suite_dir.exists():
            continue
        for path in sorted(suite_dir.glob("*.json"), key=_mtime, reverse=True):
            record = _json_file_record(
                project="agent",
                project_label=registry.PROJECT_LABELS["agent"],
                suite=suite,
                suite_label=_title(suite),
                path=path,
                root=root,
                deletable=True,
                delete_hint=None,
            )
            if record:
                records.append(record)
    return records


def _channel_records() -> list[RunRecord]:
    root = registry.channel_runs_dir()
    if not root.exists():
        return []
    records: list[RunRecord] = []
    for run_dir in _child_dirs(root):
        record = _channel_record(run_dir, root)
        if record:
            records.append(record)
    return records


def _memory_records() -> list[RunRecord]:
    root = registry.memory_reports_dir()
    if not root.exists():
        return []
    records: list[RunRecord] = []
    for report_dir in _child_dirs(root):
        if report_dir.name.startswith("memory_perf_"):
            suite, label = "memory_perf", "Memory Perf"
        elif report_dir.name.startswith("memory_quality_"):
            suite, label = "memory_quality", "Memory Quality"
        else:
            continue
        metrics = _read_json(report_dir / "metrics.json") or {}
        summary_md = _read_text(report_dir / "summary.md")
        artifacts = _artifacts(report_dir)
        summary = _memory_summary(metrics)
        passed = _infer_memory_passed(metrics, summary_md)
        record = RunRecord(
            summary=BenchmarkRunSummary(
                project="memory",
                project_label=registry.PROJECT_LABELS["memory"],
                suite=suite,
                suite_label=label,
                run_id=report_dir.name,
                title=report_dir.name,
                generated_at=_parse_datetime((metrics.get("meta") or {}).get("timestamp")),
                modified_at=_modified_at(report_dir),
                status=_status(passed),
                passed=passed,
                git_sha=(metrics.get("meta") or {}).get("git"),
                summary=summary,
                metrics=metrics,
                artifacts=artifacts,
                deletable=True,
                delete_hint=None,
            ),
            payload=metrics,
            markdown=summary_md,
            source_path=report_dir,
            root=root,
        )
        records.append(record)
    return records


def _directory_record(
    *,
    project: str,
    project_label: str,
    suite: str,
    suite_label: str,
    run_id: str,
    run_dir: Path,
    root: Path,
    deletable: bool,
) -> RunRecord | None:
    payload = _read_json(run_dir / "manifest.json") or _read_json(run_dir / "metrics.json") or {}
    summary_payload = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    cases = payload.get("cases") if isinstance(payload.get("cases"), list) else []
    passed = _bool_or_none(payload.get("passed"))
    if passed is None and isinstance(summary_payload, dict):
        failed = summary_payload.get("failed")
        passed_count = summary_payload.get("passed")
        total = summary_payload.get("total")
        if isinstance(failed, int):
            passed = failed == 0
        elif isinstance(passed_count, int) and isinstance(total, int):
            passed = passed_count == total
    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
    run_meta = payload.get("run") if isinstance(payload.get("run"), dict) else {}
    return RunRecord(
        summary=BenchmarkRunSummary(
            project=project,
            project_label=project_label,
            suite=suite,
            suite_label=suite_label,
            run_id=run_id,
            title=run_id,
            generated_at=_parse_datetime(payload.get("generated_at")),
            modified_at=_modified_at(run_dir),
            status=_status(passed),
            passed=passed,
            git_sha=run_meta.get("git_sha") or payload.get("git_sha"),
            summary=summary_payload if isinstance(summary_payload, dict) else {},
            metrics=metrics,
            artifacts=_artifacts(run_dir),
            deletable=deletable,
            delete_hint=None if deletable else "directory run is read-only",
        ),
        payload=payload,
        cases=cases,
        markdown=_read_first_text(run_dir, ("report.md", "summary.md")),
        source_path=run_dir if deletable else None,
        root=root if deletable else None,
    )


def _json_file_record(
    *,
    project: str,
    project_label: str,
    suite: str,
    suite_label: str,
    path: Path,
    root: Path,
    deletable: bool,
    delete_hint: str,
) -> RunRecord | None:
    payload = _read_json(path)
    if payload is None:
        return None
    summary_payload = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
    cases = payload.get("scenarios") if isinstance(payload.get("scenarios"), list) else []
    passed = _bool_or_none(payload.get("passed"))
    artifacts = _sidecar_artifacts(path)
    return RunRecord(
        summary=BenchmarkRunSummary(
            project=project,
            project_label=project_label,
            suite=suite,
            suite_label=suite_label,
            run_id=path.name,
            title=path.stem,
            generated_at=_parse_datetime(payload.get("generated_at")),
            modified_at=_modified_at(path),
            status=_status(passed),
            passed=passed,
            git_sha=payload.get("git_sha"),
            summary=summary_payload,
            metrics=metrics,
            artifacts=artifacts,
            deletable=deletable,
            delete_hint=None if deletable else delete_hint,
        ),
        payload=payload,
        cases=cases,
        markdown=_read_text(path.with_suffix(".md")),
        source_path=path if deletable else None,
        source_paths=_sidecar_paths(path) if deletable else [],
        root=root if deletable else None,
    )


def _channel_record(run_dir: Path, root: Path) -> RunRecord | None:
    metrics_by_runner: dict[str, dict[str, Any]] = {}
    runner_summaries: list[dict[str, Any]] = []
    all_cases: list[dict[str, Any]] = []
    for runner_dir in _child_dirs(run_dir):
        metrics = _read_json(runner_dir / "metrics.json")
        if metrics is None:
            continue
        runner = runner_dir.name
        metrics_by_runner[runner] = metrics
        runner_summaries.append(
            {
                "runner": runner,
                "run": metrics.get("run") or {},
                "summary": metrics.get("summary") or {},
                "report_html": (runner_dir / "report.html").exists(),
            }
        )
        cases = metrics.get("cases")
        if isinstance(cases, list):
            all_cases.extend(cases)
    if not runner_summaries:
        return None
    totals = _rollup_runner_summaries(runner_summaries)
    passed = totals.get("failed", 0) == 0
    git_sha = next(
        (
            (runner.get("run") or {}).get("git_sha")
            for runner in runner_summaries
            if (runner.get("run") or {}).get("git_sha")
        ),
        None,
    )
    return RunRecord(
        summary=BenchmarkRunSummary(
            project="channel",
            project_label=registry.PROJECT_LABELS["channel"],
            suite="voice",
            suite_label="Voice",
            run_id=run_dir.name,
            title=run_dir.name,
            generated_at=None,
            modified_at=_modified_at(run_dir),
            status=_status(passed),
            passed=passed,
            git_sha=git_sha,
            summary=totals,
            metrics=metrics_by_runner,
            artifacts=_artifacts(run_dir),
            deletable=True,
            delete_hint=None,
        ),
        payload={
            "runners": runner_summaries,
            "metrics": metrics_by_runner,
            "dashboard_html": (run_dir / "dashboard.html").exists(),
            "dashboard_with_room_html": (run_dir / "dashboard-with-room.html").exists(),
        },
        cases=all_cases,
        markdown=_read_first_text(run_dir, ("report.md",)),
        source_path=run_dir,
        root=root,
    )


def _rollup_runner_summaries(runners: list[dict[str, Any]]) -> dict[str, Any]:
    total = passed = failed = 0
    metrics: dict[str, Any] = {}
    for runner in runners:
        summary = runner.get("summary") or {}
        total += int(summary.get("total") or 0)
        passed += int(summary.get("passed") or 0)
        failed += int(summary.get("failed") or 0)
        for key, value in (summary.get("metrics") or {}).items():
            metrics[f"{runner['runner']}.{key}"] = value
    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "pass_rate": passed / total if total else 0.0,
        "metrics": metrics,
    }


def _memory_summary(metrics: dict[str, Any]) -> dict[str, Any]:
    if not metrics:
        return {}
    rows = []
    for section_name, section in metrics.items():
        if section_name == "meta" or not isinstance(section, dict):
            continue
        for row in section.get("table") or []:
            if isinstance(row, dict):
                rows.append({"section": section_name, **row})
    return {
        "section_count": len([k for k in metrics if k != "meta"]),
        "row_count": len(rows),
        "rows": rows[:24],
    }


def _infer_memory_passed(metrics: dict[str, Any], markdown: str | None) -> bool | None:
    text = markdown or json.dumps(metrics, ensure_ascii=False)
    lowered = text.lower()
    if any(token in lowered for token in ("fail", "failed", "❌")):
        return False
    if any(token in lowered for token in ("pass", "passed", "✅")):
        return True
    return None


def _artifacts(path: Path) -> list[BenchmarkArtifact]:
    artifacts: list[BenchmarkArtifact] = []
    if not path.exists():
        return artifacts
    for child in sorted(path.iterdir(), key=lambda item: item.name):
        if child.name.startswith("."):
            continue
        if child.is_dir():
            artifacts.append(_artifact(child, kind="directory"))
        elif child.is_file():
            artifacts.append(_artifact(child))
    return artifacts


def _sidecar_artifacts(path: Path) -> list[BenchmarkArtifact]:
    return [_artifact(sidecar) for sidecar in _sidecar_paths(path)]


def _sidecar_paths(path: Path) -> list[Path]:
    paths = [path]
    for suffix in (".md", ".html", ".log"):
        sidecar = path.with_suffix(suffix)
        if sidecar.exists():
            paths.append(sidecar)
    return paths


def _artifact(path: Path, kind: str | None = None) -> BenchmarkArtifact:
    return BenchmarkArtifact(
        name=path.name,
        kind=kind or _artifact_kind(path),
        path=str(path),
        size=path.stat().st_size if path.is_file() else None,
    )


def _artifact_kind(path: Path) -> str:
    if path.suffix == ".json" or path.suffix == ".jsonl":
        return "json"
    if path.suffix == ".md":
        return "markdown"
    if path.suffix == ".html":
        return "html"
    if path.suffix == ".log":
        return "log"
    return "other"


def _child_dirs(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(
        (path for path in root.iterdir() if path.is_dir() and not path.name.startswith(".")),
        key=_mtime,
        reverse=True,
    )


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _read_text(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _read_first_text(root: Path, names: tuple[str, ...]) -> str | None:
    for name in names:
        text = _read_text(root / name)
        if text:
            return text
    return None


def _modified_at(path: Path) -> datetime:
    return datetime.fromtimestamp(_mtime(path), tz=timezone.utc)


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _bool_or_none(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _status(passed: bool | None) -> BenchmarkStatus:
    if passed is True:
        return "passed"
    if passed is False:
        return "failed"
    return "unknown"


def _title(value: str) -> str:
    return value.replace("_", " ").replace("-", " ").title()


def _validate_segment(value: str, label: str) -> None:
    if not value or "/" in value or "\\" in value or value in {".", ".."}:
        raise HTTPException(status_code=400, detail=f"invalid {label}")
