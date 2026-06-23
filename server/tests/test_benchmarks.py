"""Tests for the unified benchmark artifact API."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient


def _isolate_benchmark_roots(monkeypatch, tmp_path: Path) -> dict[str, Path]:
    roots = {
        "agent_runs": tmp_path / "agent" / "benchmarks" / "runs",
        "agent_reports": tmp_path / "agent-debug" / "reports",
        "channel_runs": tmp_path / "channel" / "benchmarks" / "runs",
        "memory_reports": tmp_path / "memory" / "reports",
    }
    monkeypatch.setenv("EIDOLON_AGENT_BENCHMARK_RUNS_DIR", str(roots["agent_runs"]))
    monkeypatch.setenv("EIDOLON_AGENT_REPORTS_DIR", str(roots["agent_reports"]))
    monkeypatch.setenv("EIDOLON_CHANNEL_BENCHMARK_RUNS_DIR", str(roots["channel_runs"]))
    monkeypatch.setenv("EIDOLON_MEMORY_BENCHMARK_REPORTS_DIR", str(roots["memory_reports"]))
    return roots


def _write_channel_run(root: Path, run_id: str = "run-a") -> Path:
    run_dir = root / run_id
    runner_dir = run_dir / "policy"
    runner_dir.mkdir(parents=True)
    (runner_dir / "metrics.json").write_text(
        json.dumps(
            {
                "run": {
                    "run_id": run_id,
                    "runner": "policy",
                    "profile": "test",
                    "git_sha": "abc123",
                },
                "summary": {
                    "total": 2,
                    "passed": 2,
                    "failed": 0,
                    "pass_rate": 1.0,
                    "metrics": {"elapsed_ms": {"count": 2, "p50": 10, "p95": 12}},
                },
                "cases": [
                    {
                        "case_id": "case_001",
                        "suite": "core",
                        "runner": "policy",
                        "passed": True,
                        "metrics": {"elapsed_ms": 10},
                        "decisions": [],
                        "events": [],
                        "errors": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (runner_dir / "report.html").write_text("<html>ok</html>", encoding="utf-8")
    return run_dir


def _write_agent_report(root: Path) -> Path:
    report_dir = root / "realtime"
    report_dir.mkdir(parents=True)
    path = report_dir / "latest.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "eidolon_agent.realtime_benchmark_report.v1",
                "generated_at": "2026-06-23T10:00:00+00:00",
                "run_id": "latest",
                "kind": "realtime_benchmark",
                "passed": False,
                "summary": {"turn_count": 1, "failed_turn_count": 1},
                "metrics": {"first_delta_ms": {"p95": 120}},
                "scenarios": [{"scenario_id": "s1", "passed": False}],
            }
        ),
        encoding="utf-8",
    )
    (report_dir / "latest.md").write_text("# Agent report", encoding="utf-8")
    return path


def _write_memory_report(root: Path) -> Path:
    report_dir = root / "memory_perf_20260623_120000"
    report_dir.mkdir(parents=True)
    (report_dir / "metrics.json").write_text(
        json.dumps(
            {
                "meta": {
                    "timestamp": "2026-06-23T12:00:00+00:00",
                    "git": "def456",
                },
                "R-01": {
                    "table": [
                        {
                            "id": "recall",
                            "n": 3,
                            "p50": 20,
                            "p95": 30,
                            "p99": 30,
                            "max": 31,
                            "sla": "PASS",
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    (report_dir / "summary.md").write_text("# Memory\n\nPASS\n", encoding="utf-8")
    return report_dir


def test_benchmark_projects_include_empty_projects(app, tmp_path, monkeypatch):
    _isolate_benchmark_roots(monkeypatch, tmp_path)

    client = TestClient(app)
    resp = client.get("/api/benchmarks/projects")

    assert resp.status_code == 200
    projects = {item["id"]: item for item in resp.json()["projects"]}
    assert {"agent", "channel", "memory", "admin", "hub", "client-web"} <= set(projects)
    assert projects["admin"]["run_count"] == 0
    assert projects["hub"]["suites"][0]["id"] == "smoke"


def test_benchmark_list_and_detail_standardize_existing_artifacts(app, tmp_path, monkeypatch):
    roots = _isolate_benchmark_roots(monkeypatch, tmp_path)
    _write_channel_run(roots["channel_runs"])
    _write_agent_report(roots["agent_reports"])
    _write_memory_report(roots["memory_reports"])

    client = TestClient(app)
    runs_resp = client.get("/api/benchmarks/runs")

    assert runs_resp.status_code == 200
    runs = runs_resp.json()["runs"]
    by_key = {(run["project"], run["suite"], run["run_id"]): run for run in runs}
    assert ("channel", "voice", "run-a") in by_key
    assert ("agent", "realtime", "latest.json") in by_key
    assert ("memory", "memory_perf", "memory_perf_20260623_120000") in by_key
    assert by_key[("channel", "voice", "run-a")]["deletable"] is True
    assert by_key[("agent", "realtime", "latest.json")]["deletable"] is False

    detail_resp = client.get("/api/benchmarks/runs/channel/voice/run-a")

    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["status"] == "passed"
    assert detail["git_sha"] == "abc123"
    assert detail["summary"]["total"] == 2
    assert detail["cases"][0]["case_id"] == "case_001"


def test_delete_benchmark_run_moves_directory_to_trash(app, tmp_path, monkeypatch):
    roots = _isolate_benchmark_roots(monkeypatch, tmp_path)
    run_dir = _write_channel_run(roots["channel_runs"], "delete-me")

    client = TestClient(app)
    resp = client.delete("/api/benchmarks/runs/channel/voice/delete-me")

    assert resp.status_code == 200
    data = resp.json()
    trashed = Path(data["trashed_path"])
    assert not run_dir.exists()
    assert trashed.exists()
    assert trashed.parent == roots["channel_runs"] / ".trash" / "channel" / "voice"
    list_resp = client.get("/api/benchmarks/runs?project=channel&suite=voice")
    assert list_resp.status_code == 200
    assert list_resp.json()["runs"] == []


def test_delete_rejects_single_file_agent_report(app, tmp_path, monkeypatch):
    roots = _isolate_benchmark_roots(monkeypatch, tmp_path)
    _write_agent_report(roots["agent_reports"])

    client = TestClient(app)
    resp = client.delete("/api/benchmarks/runs/agent/realtime/latest.json")

    assert resp.status_code == 409
    assert "single-file" in resp.json()["detail"]


def test_benchmark_rejects_unsafe_run_id(app, tmp_path, monkeypatch):
    _isolate_benchmark_roots(monkeypatch, tmp_path)

    client = TestClient(app)
    resp = client.get("/api/benchmarks/runs/channel/voice/..%2Fsecret")

    assert resp.status_code in {400, 404}
