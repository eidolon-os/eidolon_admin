"""Schemas for the unified benchmark artifact API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

BenchmarkStatus = Literal["passed", "failed", "unknown"]


class BenchmarkArtifact(BaseModel):
    name: str
    kind: Literal["json", "markdown", "html", "log", "directory", "other"]
    path: str
    size: int | None = None


class BenchmarkRunSummary(BaseModel):
    project: str
    project_label: str
    suite: str
    suite_label: str
    run_id: str
    title: str
    generated_at: datetime | None = None
    modified_at: datetime
    status: BenchmarkStatus = "unknown"
    passed: bool | None = None
    git_sha: str | None = None
    summary: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[BenchmarkArtifact] = Field(default_factory=list)
    deletable: bool = False
    delete_hint: str | None = None


class BenchmarkRunDetail(BenchmarkRunSummary):
    cases: list[dict[str, Any]] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)
    markdown: str | None = None


class BenchmarkSuiteSummary(BaseModel):
    id: str
    label: str
    description: str | None = None
    run_count: int = 0
    latest_status: BenchmarkStatus = "unknown"
    latest_modified_at: datetime | None = None


class BenchmarkProjectSummary(BaseModel):
    id: str
    label: str
    run_count: int = 0
    latest_status: BenchmarkStatus = "unknown"
    latest_modified_at: datetime | None = None
    suites: list[BenchmarkSuiteSummary] = Field(default_factory=list)


class BenchmarkProjectsResponse(BaseModel):
    projects: list[BenchmarkProjectSummary]


class BenchmarkRunsResponse(BaseModel):
    runs: list[BenchmarkRunSummary]


class BenchmarkDeleteResponse(BaseModel):
    project: str
    suite: str
    run_id: str
    trashed_path: str
