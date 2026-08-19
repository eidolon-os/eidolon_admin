"""Wire schemas for the Mission Control runtime observatory."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from eidolon_sdk.biz.body import OwnerDeviceBlackboardSnapshot

JsonDict = dict[str, Any]

RuntimeSource = Literal[
    "hub",
    "channel",
    "agent",
    "memory",
    "data",
    "admin",
    "mission_control",
]
RuntimeSeverity = Literal["info", "warn", "error"]
# Mirrors eidolon_data's Outcome set (events.outcome column). Orthogonal to
# severity: "how loud" (severity) vs "what happened" (outcome).
RuntimeOutcome = Literal["success", "failure", "denied", "deferred"]
PrivacyMode = Literal["safe", "summary", "restricted"]
EventOrigin = Literal["live", "polling", "replay", "mock"]
DemoMode = Literal["live", "replay", "mixed"]


class SourceStatus(BaseModel):
    source: str
    ok: bool
    detail: str = ""
    latency_ms: float | None = None


class RuntimeBlackboardEntry(BaseModel):
    """One raw owner/current value read directly from the shared NATS KV."""

    key: str
    owner_id: str | None = None
    snapshot: JsonDict | None = None
    error: str = ""


class RuntimeBlackboardResponse(BaseModel):
    generated_at: datetime
    bucket: str
    owner_filter: str | None = None
    read_only: Literal[True] = True
    entries: list[RuntimeBlackboardEntry] = Field(default_factory=list)


class RuntimeEvent(BaseModel):
    event_id: str
    ts: datetime
    source: RuntimeSource
    type: str
    severity: RuntimeSeverity = "info"
    # Result classification (Phase 1). Projected from the events table where
    # available; defaults to "success" for non-audit / synthesised events.
    outcome: RuntimeOutcome = "success"
    privacy: PrivacyMode = "safe"
    # Provenance: live (SSE), polling (snapshot-derived), replay (fixture), mock.
    event_origin: EventOrigin = "polling"
    trace_id: str | None = None
    owner_id: str | None = None
    companion_id: str | None = None
    device_id: str | None = None
    conversation_id: str | None = None
    turn_id: str | None = None
    job_id: str | None = None
    summary: str
    payload: JsonDict = Field(default_factory=dict)


class RuntimeOwner(BaseModel):
    owner_id: str = ""
    display_name: str = ""
    kind: str = ""
    status: str = ""


class RuntimeCompanion(BaseModel):
    companion_id: str = ""
    display_name: str = ""
    kind: str = ""
    status: str = ""
    is_master: bool = False
    companion_type: str = "slave"
    genome_id: str | None = None
    memory_realm_id: str | None = None


class RuntimeDevice(BaseModel):
    device_id: str
    name: str = ""
    # Logical role, read from the companion this device is bound to — never
    # inferred from the hardware `kind`. `role` is the human label; `role_kind`
    # is the stable classifier: guard | persona | unbound.
    role: str = "未绑定"
    role_kind: str = "unbound"
    kind: str = "unknown"
    status: str = "offline"
    online: bool = False
    approved: bool = False
    owner_id: str | None = None
    companion_id: str | None = None
    interaction_mode: str | None = None
    room_name: str = ""
    participant_sid: str = ""
    last_seen_at: datetime | None = None
    capabilities: list[str] = Field(default_factory=list)
    signals: JsonDict = Field(default_factory=dict)


class RuntimeDeviceBlackboard(BaseModel):
    """Read-only health envelope around one owner's exact KV snapshot."""

    health: Literal["healthy", "degraded", "empty"] = "empty"
    available: bool = False
    detail: str = "No current runtime device snapshot"
    bucket: str = "EIDOLON_RUNTIME_DEVICES"
    key: str = ""
    snapshot: OwnerDeviceBlackboardSnapshot | None = None


class RuntimeTurn(BaseModel):
    turn_id: str
    trace_id: str | None = None
    channel_turn_id: str | None = None
    agent_turn_id: str | None = None
    conversation_id: str
    owner_id: str
    companion_id: str
    device_id: str | None = None
    status: str
    trigger: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None
    latency_ms: int | None = None
    memory_hits: int = 0
    tool_names: list[str] = Field(default_factory=list)
    privacy_mode: str | None = None
    phase: str = ""
    outcome: RuntimeOutcome = "success"
    terminal_reason: str = ""
    event_ids: list[str] = Field(default_factory=list)
    missing_milestones: list[str] = Field(default_factory=list)
    stages: list[JsonDict] = Field(default_factory=list)


class RuntimeRouteHop(BaseModel):
    """One factual node visited by an observed runtime activity.

    Hops are a read-only projection of persisted events, turns, and jobs. They
    are deliberately not commands and never participate in runtime routing.
    """

    hop_id: str
    node_type: str  # device | companion | service | memory | tool | provider
    node_id: str
    label: str
    stage: str = ""
    status: str = "pending"
    direction: str = "internal"  # in | out | internal
    ts: datetime | None = None
    latency_ms: int | None = None


class RuntimeActivity(BaseModel):
    """Unified observer projection for concurrent work in Mission Control."""

    activity_id: str
    kind: str  # voice_turn | guard_event | device_command | background_job
    owner_id: str = ""
    companion_id: str | None = None
    trace_id: str | None = None
    turn_id: str | None = None
    job_id: str | None = None
    origin_device_id: str | None = None
    target_device_ids: list[str] = Field(default_factory=list)
    status: str = "pending"
    outcome: RuntimeOutcome = "deferred"
    summary: str = ""
    current_hop_id: str | None = None
    started_at: datetime | None = None
    updated_at: datetime | None = None
    finished_at: datetime | None = None
    event_ids: list[str] = Field(default_factory=list)
    route: list[RuntimeRouteHop] = Field(default_factory=list)


class RuntimeJob(BaseModel):
    job_id: str
    owner_id: str
    companion_id: str | None = None
    conversation_id: str | None = None
    turn_id: str | None = None
    provider: str = ""
    kind: str = ""
    status: str = ""
    summary: str = ""
    progress: JsonDict = Field(default_factory=dict)
    result_summary: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None
    completed_at: datetime | None = None


class RuntimeMemory(BaseModel):
    realms_total: int = 0
    active_realm_id: str = ""
    runners_total: int = 0
    runners_online: int = 0
    last_recall_hits: int = 0
    last_write_disposition: str | None = None
    fanout_allowed: bool = False
    privacy_mode: str | None = None
    summary: str = ""


class RuntimeService(BaseModel):
    service_id: str
    name: str
    online: bool = False
    checked: bool = False
    latency_ms: float | None = None
    detail: str = ""


class RuntimeStoryStep(BaseModel):
    key: str
    title: str
    detail: str = ""
    status: str = "pending"
    source: str = ""
    ts: datetime | None = None


class RuntimeLaneItem(BaseModel):
    label: str
    value: str = ""
    status: str = "idle"
    detail: str = ""


class RuntimeLane(BaseModel):
    key: str
    title: str
    headline: str = ""
    detail: str = ""
    status: str = "idle"
    items: list[RuntimeLaneItem] = Field(default_factory=list)


class RuntimeCapabilityCard(BaseModel):
    key: str
    title: str
    status: str = "idle"
    metric: str = ""
    detail: str = ""


class RuntimeExperience(BaseModel):
    headline: str = "Eidolon 正在等待一次交互"
    subheadline: str = "同一个 companion 可以通过多个身体、记忆和工具协同工作。"
    plain_summary: str = "选择一个 owner 后，Mission Control 会展示身份、身体、记忆、任务和权限如何一起运转。"
    system_state: str = "standby"
    completion: int = 0
    storyline: list[RuntimeStoryStep] = Field(default_factory=list)
    lanes: list[RuntimeLane] = Field(default_factory=list)
    capability_cards: list[RuntimeCapabilityCard] = Field(default_factory=list)
    next_best_action: str = "和任意已绑定设备说一句话，观察这条链路如何被点亮。"


class RuntimeTraceSpan(BaseModel):
    """A structured span of one observed voice turn (Agent Span Inspector).
    Carries only counts/latency/labels — never prompt/transcript text."""

    span_id: str
    turn_id: str
    name: str
    kind: str  # input | memory_recall | model | tool | memory_write | routing
    status: str = "done"
    latency_ms: int | None = None
    detail: str = ""


class EvidenceStep(BaseModel):
    key: str
    label: str
    done: bool = False
    detail: str = ""


class EvidenceChain(BaseModel):
    """A demo claim with a derived proof trail (Topology + Trace + Ledger)."""

    key: str
    title: str
    claim: str
    status: str = "pending"  # pending | partial | proven
    confidence: int = 0  # done_steps / total_steps, 0..100 — honest, never faked
    steps: list[EvidenceStep] = Field(default_factory=list)


class PermissionLedgerItem(BaseModel):
    """A high-sensitivity capability invocation surfaced for audit."""

    ts: datetime | None = None
    kind: str  # camera.take_photo | room.join | device.identify | device.command | ...
    device_id: str | None = None
    status: str = ""
    privacy_level: str = "operation"  # sensitive | operation
    raw_retention: str = "n/a"  # not_stored | n/a
    summary: str = ""


class RuntimeSnapshot(BaseModel):
    generated_at: datetime
    owner: RuntimeOwner | None = None
    companion: RuntimeCompanion | None = None
    companions: list[RuntimeCompanion] = Field(default_factory=list)
    devices: list[RuntimeDevice] = Field(default_factory=list)
    services: list[RuntimeService] = Field(default_factory=list)
    activities: list[RuntimeActivity] = Field(default_factory=list)
    recent_turns: list[RuntimeTurn] = Field(default_factory=list)
    memory: RuntimeMemory = Field(default_factory=RuntimeMemory)
    jobs: list[RuntimeJob] = Field(default_factory=list)
    recent_events: list[RuntimeEvent] = Field(default_factory=list)
    source_status: list[SourceStatus] = Field(default_factory=list)
    runtime_blackboard: RuntimeDeviceBlackboard = Field(
        default_factory=RuntimeDeviceBlackboard
    )
    experience: RuntimeExperience = Field(default_factory=RuntimeExperience)
    trace_spans: list[RuntimeTraceSpan] = Field(default_factory=list)
    evidence_chains: list[EvidenceChain] = Field(default_factory=list)
    permission_ledger: list[PermissionLedgerItem] = Field(default_factory=list)
    demo_mode: DemoMode = "live"
    privacy_notice: str = "Default safe mode: raw transcripts, messages, and images are redacted."
