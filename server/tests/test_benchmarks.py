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
        "admin_runs": tmp_path / "admin" / "benchmarks" / "runs",
        "hub_runs": tmp_path / "hub" / "benchmarks" / "runs",
        "client_web_runs": tmp_path / "client-web" / "benchmarks" / "runs",
        "memory_runs": tmp_path / "memory" / "benchmarks" / "runs",
        "memory_reports": tmp_path / "memory" / "reports",
    }
    monkeypatch.setenv("EIDOLON_AGENT_BENCHMARK_RUNS_DIR", str(roots["agent_runs"]))
    monkeypatch.setenv("EIDOLON_AGENT_REPORTS_DIR", str(roots["agent_reports"]))
    monkeypatch.setenv("EIDOLON_CHANNEL_BENCHMARK_RUNS_DIR", str(roots["channel_runs"]))
    monkeypatch.setenv("EIDOLON_ADMIN_BENCHMARK_RUNS_DIR", str(roots["admin_runs"]))
    monkeypatch.setenv("EIDOLON_HUB_BENCHMARK_RUNS_DIR", str(roots["hub_runs"]))
    monkeypatch.setenv(
        "EIDOLON_CLIENT_WEB_BENCHMARK_RUNS_DIR", str(roots["client_web_runs"])
    )
    monkeypatch.setenv("EIDOLON_MEMORY_BENCHMARK_RUNS_DIR", str(roots["memory_runs"]))
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


def _write_agent_project_file_run(root: Path) -> Path:
    suite_dir = root / "live-memory-e2e"
    suite_dir.mkdir(parents=True)
    path = suite_dir / "manson.json"
    path.write_text(
        json.dumps(
            {
                "generated_at": "2026-06-05T10:03:11+00:00",
                "mode": "live_service",
                "passed": False,
                "summary": {"scenario_count": 11, "passed": 5, "failed": 6},
                "metrics": {"turn_count": 24},
                "scenarios": [{"scenario_id": "s1", "passed": False}],
            }
        ),
        encoding="utf-8",
    )
    (suite_dir / "manson.md").write_text("# Replay Report\n", encoding="utf-8")
    return path


def _write_standard_run(root: Path, *, suite: str, run_id: str) -> Path:
    run_dir = root / suite / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-07-10T08:00:00+00:00",
                "run_id": run_id,
                "passed": True,
                "summary": {"total": 1, "passed": 1, "failed": 0},
                "metrics": {"elapsed_ms": {"p95": 12}},
                "cases": [{"case_id": "smoke", "passed": True}],
            }
        ),
        encoding="utf-8",
    )
    return run_dir


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


def _write_memory_readable_report(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / "memory_benchmark_readable_20260624.md"
    path.write_text(
        "# Eidolon Memory Benchmark 中文报告\n\n"
        "## 一句话结论\n\n"
        "- 最终 run 通过。\n"
        "- 没有 other-device leakage。\n",
        encoding="utf-8",
    )
    return path


def test_benchmark_projects_include_empty_projects(app, tmp_path, monkeypatch):
    _isolate_benchmark_roots(monkeypatch, tmp_path)

    client = TestClient(app)
    resp = client.get("/api/benchmarks/projects")

    assert resp.status_code == 200
    projects = {item["id"]: item for item in resp.json()["projects"]}
    assert {"agent", "channel", "memory", "admin", "hub", "client-web"} <= set(projects)
    assert projects["admin"]["run_count"] == 0
    assert projects["hub"]["suites"][0]["id"] == "smoke"
    memory_suites = {suite["id"]: suite for suite in projects["memory"]["suites"]}
    agent_suites = {suite["id"]: suite for suite in projects["agent"]["suites"]}
    assert agent_suites["persona_memory"]["description"]
    assert memory_suites["memory_perf"]["description"]
    assert memory_suites["memory_readable"]["description"]


def test_benchmark_list_and_detail_standardize_existing_artifacts(app, tmp_path, monkeypatch):
    roots = _isolate_benchmark_roots(monkeypatch, tmp_path)
    _write_channel_run(roots["channel_runs"])
    _write_agent_report(roots["agent_reports"])
    _write_agent_project_file_run(roots["agent_runs"])
    _write_memory_report(roots["memory_reports"])
    _write_memory_readable_report(roots["memory_reports"])
    _write_standard_run(roots["admin_runs"], suite="smoke", run_id="admin-smoke")

    client = TestClient(app)
    runs_resp = client.get("/api/benchmarks/runs")

    assert runs_resp.status_code == 200
    runs = runs_resp.json()["runs"]
    by_key = {(run["project"], run["suite"], run["run_id"]): run for run in runs}
    assert ("channel", "voice", "run-a") in by_key
    assert ("agent", "realtime", "latest.json") in by_key
    assert ("agent", "live-memory-e2e", "manson.json") in by_key
    assert ("memory", "memory_perf", "memory_perf_20260623_120000") in by_key
    assert ("memory", "memory_readable", "memory_benchmark_readable_20260624.md") in by_key
    assert ("admin", "smoke", "admin-smoke") in by_key
    assert by_key[("channel", "voice", "run-a")]["deletable"] is True
    assert by_key[("agent", "live-memory-e2e", "manson.json")]["deletable"] is True
    assert by_key[("agent", "realtime", "latest.json")]["deletable"] is True

    detail_resp = client.get("/api/benchmarks/runs/channel/voice/run-a")

    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["status"] == "passed"
    assert detail["git_sha"] == "abc123"
    assert detail["summary"]["total"] == 2
    assert detail["cases"][0]["case_id"] == "case_001"

    readable_resp = client.get(
        "/api/benchmarks/runs/memory/memory_readable/memory_benchmark_readable_20260624.md"
    )
    assert readable_resp.status_code == 200
    readable = readable_resp.json()
    assert readable["status"] == "passed"
    assert readable["markdown"].startswith("# Eidolon Memory Benchmark 中文报告")
    assert readable["summary"]["kind"] == "readable_report"


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


def test_delete_agent_project_file_run_moves_sidecars_to_trash(app, tmp_path, monkeypatch):
    roots = _isolate_benchmark_roots(monkeypatch, tmp_path)
    report = _write_agent_project_file_run(roots["agent_runs"])
    markdown = report.with_suffix(".md")

    client = TestClient(app)
    resp = client.delete("/api/benchmarks/runs/agent/live-memory-e2e/manson.json")

    assert resp.status_code == 200
    trashed = Path(resp.json()["trashed_path"])
    assert not report.exists()
    assert not markdown.exists()
    assert (trashed / "manson.json").exists()
    assert (trashed / "manson.md").exists()
    assert trashed.parent == roots["agent_runs"] / ".trash" / "agent" / "live-memory-e2e"


def test_delete_agent_debug_report_moves_sidecars_to_trash(app, tmp_path, monkeypatch):
    roots = _isolate_benchmark_roots(monkeypatch, tmp_path)
    report = _write_agent_report(roots["agent_reports"])
    markdown = report.with_suffix(".md")

    client = TestClient(app)
    resp = client.delete("/api/benchmarks/runs/agent/realtime/latest.json")

    assert resp.status_code == 200
    trashed = Path(resp.json()["trashed_path"])
    assert not report.exists()
    assert not markdown.exists()
    assert (trashed / "latest.json").exists()
    assert (trashed / "latest.md").exists()
    assert trashed.parent == roots["agent_reports"] / ".trash" / "agent" / "realtime"


def test_benchmark_rejects_unsafe_run_id(app, tmp_path, monkeypatch):
    _isolate_benchmark_roots(monkeypatch, tmp_path)

    client = TestClient(app)
    resp = client.get("/api/benchmarks/runs/channel/voice/..%2Fsecret")

    assert resp.status_code in {400, 404}
