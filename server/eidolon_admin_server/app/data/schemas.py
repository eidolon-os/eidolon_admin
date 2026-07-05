"""Schemas for admin's owner-scoped Eidolon Data API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

JsonDict = dict[str, Any]


class OwnerCreateRequest(BaseModel):
    owner_id: str
    display_name: str = ""
    kind: str = "person"
    profile_json: JsonDict = Field(default_factory=dict)
    settings_json: JsonDict = Field(default_factory=dict)


class OwnerUpdateRequest(BaseModel):
    display_name: str | None = None
    kind: str | None = None
    profile_json: JsonDict | None = None
    settings_json: JsonDict | None = None


class OwnerView(BaseModel):
    owner_id: str
    display_name: str
    kind: str
    status: str
    profile_json: JsonDict = Field(default_factory=dict)
    settings_json: JsonDict = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class OwnerListResponse(BaseModel):
    owners: list[OwnerView]


class CompanionView(BaseModel):
    companion_id: str
    owner_id: str
    display_name: str
    kind: str
    status: str
    is_master: bool = False
    current_genome_id: str | None = None
    default_memory_realm_id: str | None = None
    profile_json: JsonDict = Field(default_factory=dict)
    runtime_config_json: JsonDict = Field(default_factory=dict)
    metadata_json: JsonDict = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class CompanionListResponse(BaseModel):
    companions: list[CompanionView]


class WorkspaceInitializeRequest(BaseModel):
    companion_id: str | None = None
    companion_display_name: str = ""
    companion_kind: str = "companion"
    companion_profile_json: JsonDict = Field(default_factory=dict)
    companion_runtime_config_json: JsonDict = Field(default_factory=dict)
    companion_metadata_json: JsonDict = Field(default_factory=dict)
    genome_id: str | None = None
    genome_source_json: JsonDict = Field(default_factory=dict)
    genome_json: JsonDict = Field(default_factory=dict)
    prompt_markdown: str = ""
    evolution_state_json: JsonDict = Field(default_factory=dict)
    realm_id: str | None = None
    memory_engine: str = "mempalace"
    memory_engine_config_json: JsonDict = Field(default_factory=dict)
    memory_policy_json: JsonDict = Field(default_factory=dict)


class PersonaGenomeView(BaseModel):
    genome_id: str
    companion_id: str
    version: int
    status: str
    base_genome_id: str | None = None
    source_json: JsonDict = Field(default_factory=dict)
    genome_json: JsonDict = Field(default_factory=dict)
    prompt_markdown: str = ""
    evolution_state_json: JsonDict = Field(default_factory=dict)
    change_summary: str = ""
    created_at: datetime
    updated_at: datetime


class PersonaGenomeListResponse(BaseModel):
    persona_genomes: list[PersonaGenomeView]


class DeviceView(BaseModel):
    device_id: str
    owner_id: str | None = None
    name: str
    kind: str
    status: str
    approved_at: datetime | None = None
    approved_by: str | None = None
    bound_companion_id: str | None = None
    interaction_mode: str | None = None
    auth_type: str | None = None
    capabilities_json: JsonDict = Field(default_factory=dict)
    network_json: JsonDict = Field(default_factory=dict)
    access_policy_json: JsonDict = Field(default_factory=dict)
    metadata_json: JsonDict = Field(default_factory=dict)
    last_seen_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    revoked_at: datetime | None = None


class DeviceListResponse(BaseModel):
    devices: list[DeviceView]


class NearbyDeviceView(BaseModel):
    device_id: str
    name: str = ""
    kind: str = "unknown"
    enabled: bool = True
    approved: bool = False
    status: str = "unknown"
    room_name: str = ""
    missed_probes: int = 0
    last_seen: datetime | None = None


class NearbyDeviceListResponse(BaseModel):
    devices: list[NearbyDeviceView]
    hub_available: bool = True


class DeviceClaimRequest(BaseModel):
    name: str | None = None
    companion_id: str | None = None
    interaction_mode: str | None = None
    access_policy_json: JsonDict = Field(default_factory=dict)
    metadata_json: JsonDict = Field(default_factory=dict)


DeviceAddToOwnerRequest = DeviceClaimRequest


class DeviceUpdateRequest(BaseModel):
    name: str | None = None
    metadata_json: JsonDict | None = None


class ConversationView(BaseModel):
    conversation_id: str
    owner_id: str
    companion_id: str
    runtime_caller_id: str | None = None
    runtime_session_id: str | None = None
    device_id: str | None = None
    title: str | None = None
    status: str
    started_at: datetime
    updated_at: datetime
    ended_at: datetime | None = None
    metadata_json: JsonDict = Field(default_factory=dict)


class ConversationListResponse(BaseModel):
    conversations: list[ConversationView]


class MemoryRealmView(BaseModel):
    realm_id: str
    owner_id: str
    companion_id: str
    engine: str
    engine_config_json: JsonDict = Field(default_factory=dict)
    policy_json: JsonDict = Field(default_factory=dict)
    status: str
    created_at: datetime
    updated_at: datetime


class MemoryRealmListResponse(BaseModel):
    memory_realms: list[MemoryRealmView]


class JobView(BaseModel):
    job_id: str
    owner_id: str
    companion_id: str | None = None
    conversation_id: str | None = None
    turn_id: str | None = None
    provider: str
    kind: str
    status: str
    input_json: JsonDict = Field(default_factory=dict)
    provider_ref_json: JsonDict = Field(default_factory=dict)
    progress_json: JsonDict = Field(default_factory=dict)
    result_json: JsonDict = Field(default_factory=dict)
    error_json: JsonDict = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


class JobListResponse(BaseModel):
    jobs: list[JobView]


class EventView(BaseModel):
    event_id: str
    owner_id: str
    companion_id: str | None = None
    subject_type: str
    subject_id: str
    event_type: str
    event_class: str = "audit"
    source: str = "data"
    severity: str = "info"
    outcome: str = "success"
    reason: str | None = None
    actor_type: str
    actor_id: str | None = None
    trace_id: str | None = None
    data_classification: str = "safe"
    payload_json: JsonDict = Field(default_factory=dict)
    occurred_at: datetime | None = None
    created_at: datetime


class EventListResponse(BaseModel):
    events: list[EventView]


class OwnerCounts(BaseModel):
    companions: int
    persona_genomes: int
    devices: int
    conversations: int
    memory_realms: int
    jobs: int
    events: int


class OwnerOverviewResponse(BaseModel):
    owner: OwnerView
    counts: OwnerCounts
    initialized: bool
    companions: list[CompanionView]
    devices: list[DeviceView]
    conversations: list[ConversationView]
    memory_realms: list[MemoryRealmView]
    jobs: list[JobView]
    events: list[EventView]


class WorkspaceInitializeResponse(BaseModel):
    companion: CompanionView
    persona_genome: PersonaGenomeView
    memory_realm: MemoryRealmView
