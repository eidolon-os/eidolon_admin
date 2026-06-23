"""Unified benchmark artifact API."""

from __future__ import annotations

from fastapi import APIRouter

from . import adapters, registry
from .schemas import (
    BenchmarkDeleteResponse,
    BenchmarkProjectSummary,
    BenchmarkProjectsResponse,
    BenchmarkRunDetail,
    BenchmarkRunsResponse,
    BenchmarkStatus,
    BenchmarkSuiteSummary,
)

router = APIRouter(prefix="/benchmarks", tags=["benchmarks"])


@router.get("/projects", response_model=BenchmarkProjectsResponse)
def list_projects() -> BenchmarkProjectsResponse:
    runs = adapters.list_runs()
    suites_by_project = adapters.known_suites()
    projects: list[BenchmarkProjectSummary] = []
    for project in registry.PROJECTS:
        project_runs = [run for run in runs if run.project == project.id]
        suite_rows: list[BenchmarkSuiteSummary] = []
        for suite_id, suite_label in suites_by_project.get(project.id, {}).items():
            suite_runs = [run for run in project_runs if run.suite == suite_id]
            latest = suite_runs[0] if suite_runs else None
            suite_rows.append(
                BenchmarkSuiteSummary(
                    id=suite_id,
                    label=suite_label,
                    run_count=len(suite_runs),
                    latest_status=latest.status if latest else "unknown",
                    latest_modified_at=latest.modified_at if latest else None,
                )
            )
        latest_project = project_runs[0] if project_runs else None
        projects.append(
            BenchmarkProjectSummary(
                id=project.id,
                label=project.label,
                run_count=len(project_runs),
                latest_status=_latest_status(project_runs),
                latest_modified_at=latest_project.modified_at if latest_project else None,
                suites=sorted(suite_rows, key=lambda suite: suite.id),
            )
        )
    return BenchmarkProjectsResponse(projects=projects)


@router.get("/runs", response_model=BenchmarkRunsResponse)
def list_runs(project: str | None = None, suite: str | None = None) -> BenchmarkRunsResponse:
    return BenchmarkRunsResponse(runs=adapters.list_runs(project=project, suite=suite))


@router.get("/runs/{project}/{suite}/{run_id}", response_model=BenchmarkRunDetail)
def get_run(project: str, suite: str, run_id: str) -> BenchmarkRunDetail:
    return adapters.get_run(project, suite, run_id)


@router.delete("/runs/{project}/{suite}/{run_id}", response_model=BenchmarkDeleteResponse)
def delete_run(project: str, suite: str, run_id: str) -> BenchmarkDeleteResponse:
    trashed_path = adapters.delete_run(project, suite, run_id)
    return BenchmarkDeleteResponse(
        project=project,
        suite=suite,
        run_id=run_id,
        trashed_path=str(trashed_path),
    )


def _latest_status(runs) -> BenchmarkStatus:
    if not runs:
        return "unknown"
    latest = runs[0]
    return latest.status
