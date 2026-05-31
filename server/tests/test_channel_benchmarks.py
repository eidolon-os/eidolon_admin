"""Tests for Channel benchmark artifact API."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient


def _write_metrics(run_dir: Path, runner: str, *, passed: int = 1, total: int = 1) -> None:
    target = run_dir / runner
    target.mkdir(parents=True)
    (target / "metrics.json").write_text(
        json.dumps(
            {
                "run": {
                    "run_id": run_dir.name,
                    "runner": runner,
                    "profile": "test",
                    "git_sha": "abc123",
                },
                "summary": {
                    "total": total,
                    "passed": passed,
                    "failed": total - passed,
                    "pass_rate": passed / total,
                    "metrics": {
                        "elapsed_ms": {
                            "count": total,
                            "avg": 10,
                            "p50": 10,
                            "p95": 10,
                            "max": 10,
                        }
                    },
                },
                "cases": [
                    {
                        "case_id": "case_001",
                        "suite": "s",
                        "runner": runner,
                        "passed": passed == total,
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


def test_list_channel_benchmark_runs(app, tmp_path, monkeypatch):
    run_dir = tmp_path / "runs" / "run-a"
    _write_metrics(run_dir, "policy")
    monkeypatch.setenv("EIDOLON_CHANNEL_BENCHMARK_RUNS_DIR", str(tmp_path / "runs"))

    client = TestClient(app)
    resp = client.get("/api/channel/benchmarks/runs")

    assert resp.status_code == 200
    data = resp.json()
    assert data["runs_dir"] == str(tmp_path / "runs")
    assert data["runs"][0]["run_id"] == "run-a"
    assert data["runs"][0]["runners"][0]["runner"] == "policy"
    assert data["runs"][0]["runners"][0]["summary"]["passed"] == 1


def test_get_channel_benchmark_run_detail(app, tmp_path, monkeypatch):
    run_dir = tmp_path / "runs" / "run-a"
    _write_metrics(run_dir, "policy")
    _write_metrics(run_dir, "component", passed=2, total=2)
    monkeypatch.setenv("EIDOLON_CHANNEL_BENCHMARK_RUNS_DIR", str(tmp_path / "runs"))

    client = TestClient(app)
    resp = client.get("/api/channel/benchmarks/runs/run-a")

    assert resp.status_code == 200
    data = resp.json()
    assert set(data["metrics"]) == {"policy", "component"}
    assert data["metrics"]["component"]["summary"]["passed"] == 2


def test_channel_benchmark_rejects_unsafe_run_id(app, tmp_path, monkeypatch):
    monkeypatch.setenv("EIDOLON_CHANNEL_BENCHMARK_RUNS_DIR", str(tmp_path / "runs"))

    client = TestClient(app)
    resp = client.get("/api/channel/benchmarks/runs/..%2Fsecret")

    assert resp.status_code in {400, 404}
