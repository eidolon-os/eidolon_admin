"""Pydantic schemas for the memory module.

All request/response models live here so individual router files stay focused
on routing + dependency wiring. Names mirror eidolon_memory's admin schemas to
ease the migration; we deliberately re-declare instead of importing so admin
stays decoupled from the memory package.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

# -- Realms -------------------------------------------------------------------


class ConsolidatorStatus(BaseModel):
    configured: bool = False
    enabled: bool = False
    interval_hours: float | None = None
    window_days: int | None = None
    min_drawers: int | None = None
    min_confidence: float | None = None
    running: bool = False
    pid: int | None = None
    uptime_sec: int | None = None
    log_path: str = ""


class ConsolidatorUpdateRequest(BaseModel):
    enabled: bool
    interval_hours: float = Field(gt=0, default=6.0)
    window_days: int = Field(gt=0, default=30)
    min_drawers: int = Field(ge=1, default=3)
    min_confidence: float = Field(ge=0.0, le=1.0, default=0.6)


class RealmDetail(BaseModel):
    memory_realm_id: str
    owner_id: str
    companion_id: str
    companion_display_name: str = ""
    port: int
    enabled: bool
    engine: str = "mempalace"
    mempalace_version: str = ""
    configured_backend: str = "chroma"
    backend_state: str = "unknown"
    backend_issue: str = ""
    backend_artifacts: list[dict[str, Any]] = Field(default_factory=list)
    status: str = "active"
    palace_path: str = ""
    mcp_http_url: str = ""
    agent_reachable: bool = False
    worker_running: bool = False
    runtime_state: str = "disabled"
    palace_initialized: bool = False
    managed_by_admin: bool = False
    pid: int | None = None
    log_path: str | None = None
    agent_log_path: str = ""
    consolidator: ConsolidatorStatus | None = None
    runner_status: dict[str, Any] | None = None


class RealmsListResponse(BaseModel):
    realms_source: str
    default_memory_realm_id: str = ""
    realms: list[RealmDetail] = Field(default_factory=list)


class RebuildIndexJob(BaseModel):
    job_id: str
    memory_realm_id: str
    status: str
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    log_path: str = ""
    error: str | None = None
    result: dict[str, Any] | None = None


class RebuildIndexJobsResponse(BaseModel):
    jobs: list[RebuildIndexJob] = Field(default_factory=list)


class MemoryReconcileResponse(BaseModel):
    ok: bool = True


# -- Memories -----------------------------------------------------------------


class MemorySearchResponse(BaseModel):
    records: list[dict[str, Any]] = Field(default_factory=list)


class MemoryListResponse(BaseModel):
    records: list[dict[str, Any]] = Field(default_factory=list)
    total_hint: int = 0


class MemoryCommandStatusResponse(BaseModel):
    request_id: str
    status: str
    kind: str = ""
    resource_id: str | None = None
    error: str | None = None
    attempts: int = 0
    created_at: str = ""
    updated_at: str = ""


class MemoryCreateRequest(BaseModel):
    memory_realm_id: str
    wing: str = "Wing_Profile"
    room: str = "profile_core"
    text: str = Field(..., min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryWriteAccepted(BaseModel):
    status: str = "accepted"
    detail: str


# -- Hierarchy ----------------------------------------------------------------


class HierarchyResponse(BaseModel):
    """Free-form passthrough — agent's tool returns a rich nested dict."""
    data: dict[str, Any] = Field(default_factory=dict)


# -- Graph --------------------------------------------------------------------


def _none_to_empty_str(v: Any) -> Any:
    """Coerce None → '' so memory tool responses with null string fields don't
    blow up Pydantic's strict string validation. Applied via field_validator
    below; we leave id-like fields strict (they should never be None)."""
    return "" if v is None else v


class GraphNodeOut(BaseModel):
    id: str
    label: str = ""
    kind: str = ""            # "entity" | "room"
    entity_type: str = ""

    @field_validator("label", "kind", "entity_type", mode="before")
    @classmethod
    def _empty(cls, v: Any) -> Any:
        return _none_to_empty_str(v)


class GraphEdgeOut(BaseModel):
    source: str
    target: str
    label: str = ""
    valid_from: str | None = None
    valid_to: str | None = None
    current: bool = True

    @field_validator("label", mode="before")
    @classmethod
    def _empty(cls, v: Any) -> Any:
        return _none_to_empty_str(v)


class GraphSnapshot(BaseModel):
    available: bool = True
    palace_path: str = ""
    nodes: list[GraphNodeOut] = Field(default_factory=list)
    edges: list[GraphEdgeOut] = Field(default_factory=list)
    capped: bool = False
    reason: str = ""

    @field_validator("palace_path", "reason", mode="before")
    @classmethod
    def _empty(cls, v: Any) -> Any:
        return _none_to_empty_str(v)


# -- KG -----------------------------------------------------------------------


class KgPredicates(BaseModel):
    predicates: list[str] = Field(default_factory=list)
    sensitive: list[str] = Field(default_factory=list)


class KgStats(BaseModel):
    entities: int = 0
    triples_total: int = 0
    triples_active: int = 0
    triples_invalidated: int = 0


class KgTripleOut(BaseModel):
    id: str | None = None
    subject: str
    predicate: str
    object: str
    valid_from: str | None = None
    valid_to: str | None = None
    confidence: float | None = None
    source_drawer_id: str | None = None
    adapter_name: str | None = None


class KgEntityResponse(BaseModel):
    entity: str
    triples: list[KgTripleOut] = Field(default_factory=list)


class KgTimelineResponse(BaseModel):
    triples: list[KgTripleOut] = Field(default_factory=list)


class KgTripleAddRequest(BaseModel):
    memory_realm_id: str
    subject: str = Field(..., min_length=1)
    predicate: str = Field(..., min_length=1)
    object: str = Field(..., min_length=1)
    confidence: float = 1.0
    valid_from: str | None = None
    valid_to: str | None = None
    wait_visible_seconds: float = 2.0


class KgInvalidateRequest(BaseModel):
    memory_realm_id: str
    subject: str = Field(..., min_length=1)
    predicate: str = Field(..., min_length=1)
    object: str = Field(..., min_length=1)
    ended: str | None = None
    wait_visible_seconds: float = 2.0


class KgWriteResult(BaseModel):
    status: str
    request_id: str | None = None
    triple_id: str | None = None


# -- Recall -------------------------------------------------------------------


class RecallRequest(BaseModel):
    query: str = Field(..., min_length=1)
    top_k: int = 5
    voice: bool = False
    include_kg: bool | None = None
    include_sensitive_kg: bool = False


class RecallResponse(BaseModel):
    context: str = ""
    kg_triples: list[KgTripleOut] = Field(default_factory=list)
    records: list[dict[str, Any]] = Field(default_factory=list)


# -- MCP tools ----------------------------------------------------------------


class McpToolOut(BaseModel):
    name: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)


class McpToolsResponse(BaseModel):
    tools: list[McpToolOut] = Field(default_factory=list)
    count: int = 0
