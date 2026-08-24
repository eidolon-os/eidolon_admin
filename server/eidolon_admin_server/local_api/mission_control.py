"""Owner-scoped runtime projection for the Mobile star map.

The contract is `eidolon_sdk/contracts/local_api/v1/mission-control-snapshot.schema.json`
and its vocabulary is imported from the SDK below, so this producer cannot drift
from the schema its consumer was written against.

Two shapes carry the whole design:

Every block is a *lane* with its own health. One authority failing costs one
lane, not the screen — a Host whose memory service is down still has devices and
companions worth looking at. And a lane that could not be read says so, rather
than arriving as an empty list: this codebase has already paid a day for a
failure that reached a screen looking exactly like "nothing happened".

Presence is never inferred. This boundary has no port to the runtime blackboard
and none to Hub's per-device status, so every device here reports
``unknown / none`` — nobody answered — and the consumer renders that as unprobed
rather than offline. Turning a Kernel mount or a Data lifecycle state into an
online verdict would be the exact lie the contract forbids.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Generic, Literal, TypeVar

from eidolon_sdk.biz.contracts.companion import CompanionLifecycleState
from eidolon_sdk.biz.contracts import mission_control as contract
from pydantic import BaseModel, ConfigDict, Field

from ..app.control_plane.contracts import (
    HubDevice,
    KernelMount,
    OwnerInventory,
    SourceStatus,
)
from .host_services import LocalHostServiceInventoryView, RuntimeState

ItemT = TypeVar("ItemT")
ValueT = TypeVar("ValueT")

LaneState = Literal["ok", "degraded", "unavailable"]


class _Lane(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: LaneState
    detail: str = Field(default="", max_length=256)
    observed_at: datetime | None = None
    latency_ms: float | None = Field(default=None, ge=0)


class ItemsLane(_Lane, Generic[ItemT]):
    """A list, plus whether it was fully read."""

    truncated: bool = False
    items: tuple[ItemT, ...] = ()


class ValueLane(_Lane, Generic[ValueT]):
    value: ValueT | None = None


def _ok_items(
    items: tuple[ItemT, ...],
    *,
    truncated: bool = False,
    detail: str = "",
    state: LaneState = "ok",
) -> ItemsLane[ItemT]:
    return ItemsLane[ItemT](
        state=state,
        detail=detail,
        observed_at=datetime.now(UTC),
        truncated=truncated,
        items=items,
    )


def _missing_items(detail: str) -> ItemsLane[ItemT]:
    return ItemsLane[ItemT](state="unavailable", detail=detail)


# ── projected shapes ───────────────────────────────────────────────────────


class MissionControlOwner(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner_id: str = Field(min_length=1, max_length=64)
    # Empty stays empty. An identifier is what someone falls back to when
    # nobody will tell them what a thing is.
    display_name: str = Field(default="", max_length=128)


class MissionControlCompanion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    companion_id: str = Field(min_length=1, max_length=64)
    display_name: str = Field(default="", max_length=128)
    is_primary: bool = False
    #: The Companion authority's vocabulary, imported rather than restated. This
    #: line said active/pending/suspended/removed, which no Host can send: an
    #: archived Companion had no representable value, so the first real roster
    #: through here would have failed to validate.
    lifecycle_state: CompanionLifecycleState
    # Ownership of a persona, not whether one is loaded. Null means unbound.
    genome_id: str | None = Field(default=None, max_length=64)
    memory_realm_id: str | None = Field(default=None, max_length=64)


class MissionControlPresence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: Literal["online", "offline", "degraded", "unknown"]
    source: Literal["runtime_blackboard", "hub", "none"]
    observed_at: datetime | None = None
    lease_expires_at: datetime | None = None


class MissionControlDevice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(min_length=1, max_length=128)
    display_name: str = Field(default="", max_length=128)
    device_kind: str = Field(default="", max_length=96)
    role: str = Field(default="", max_length=64)
    role_kind: Literal["guard", "persona", "unbound"] = "unbound"
    companion_id: str | None = Field(default=None, max_length=64)
    capabilities: tuple[str, ...] = ()
    presence: MissionControlPresence


class MissionControlHop(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hop_id: str = Field(min_length=1, max_length=64)
    node_type: Literal["device", "companion", "service", "memory", "tool", "provider"]
    node_id: str = Field(default="", max_length=128)
    label: str = Field(min_length=1, max_length=96)
    stage: str = Field(default="", max_length=32)
    status: str = Field(min_length=1, max_length=32)
    direction: Literal["in", "out", "internal"] = "internal"
    ts: datetime | None = None
    latency_ms: float | None = Field(default=None, ge=0)


class MissionControlActivity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    activity_id: str = Field(min_length=1, max_length=64)
    kind: Literal[
        "voice_turn",
        "guard_event",
        "device_command",
        "device_event",
        "background_job",
    ]
    companion_id: str | None = Field(default=None, max_length=64)
    status: str = Field(min_length=1, max_length=32)
    outcome: Literal["success", "failure", "denied", "deferred"]
    summary: str = Field(default="", max_length=512)
    turn_id: str | None = Field(default=None, max_length=64)
    origin_device_id: str | None = Field(default=None, max_length=128)
    target_device_ids: tuple[str, ...] = ()
    current_hop_id: str | None = Field(default=None, max_length=64)
    started_at: datetime | None = None
    updated_at: datetime | None = None
    finished_at: datetime | None = None
    route: tuple[MissionControlHop, ...] = ()


class MissionControlStage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=32)
    label: str = Field(min_length=1, max_length=32)
    status: str = Field(min_length=1, max_length=32)
    latency_ms: float | None = Field(default=None, ge=0)


class MissionControlTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turn_id: str = Field(min_length=1, max_length=64)
    companion_id: str = Field(min_length=1, max_length=64)
    device_id: str | None = Field(default=None, max_length=128)
    status: str = Field(min_length=1, max_length=32)
    trigger: str = Field(default="", max_length=32)
    latency_ms: float | None = Field(default=None, ge=0)
    # A companion's recall is read from here. Asking the memory service per
    # companion would be a cross-service read for a number already in hand.
    memory_hits: int | None = Field(default=None, ge=0)
    tool_names: tuple[str, ...] = ()
    stages: tuple[MissionControlStage, ...] = ()


class MissionControlJob(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(min_length=1, max_length=64)
    companion_id: str | None = Field(default=None, max_length=64)
    kind: str = Field(min_length=1, max_length=64)
    status: str = Field(min_length=1, max_length=32)
    summary: str = Field(default="", max_length=512)


class MissionControlMemory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    realms_total: int = Field(default=0, ge=0)
    active_realm_id: str = Field(default="", max_length=64)
    runners_online: int | None = Field(default=None, ge=0)
    runners_total: int | None = Field(default=None, ge=0)
    last_write_disposition: str = Field(default="", max_length=64)


class MissionControlService(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service_id: str = Field(min_length=1, max_length=64)
    display_name: str = Field(default="", max_length=64)
    code: str = Field(default="", max_length=64)
    mode: str = Field(default="", max_length=32)
    tier: Literal["service", "middleware", "external"]
    online: bool
    # Whether anybody actually probed it. An unprobed service is unknown, not
    # healthy, and this is the bit that stops a client assuming otherwise.
    checked: bool
    latency_ms: float | None = Field(default=None, ge=0)
    detail: str = Field(default="", max_length=256)


class MissionControlEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1, max_length=64)
    ingest_seq: int = Field(ge=1)
    producer_seq: int | None = Field(default=None, ge=1)
    ts: datetime
    source: str = Field(min_length=1, max_length=64)
    type: str = Field(min_length=1, max_length=128)
    severity: Literal["info", "warn", "error", "critical"] = "info"
    outcome: Literal["success", "failure", "denied", "deferred"] = "success"
    privacy: Literal["safe", "sensitive", "restricted"] = "safe"
    origin: Literal["live", "polling", "replay"] = "live"
    trace_id: str | None = Field(default=None, max_length=64)
    companion_id: str | None = Field(default=None, max_length=64)
    device_id: str | None = Field(default=None, max_length=128)
    turn_id: str | None = Field(default=None, max_length=64)
    job_id: str | None = Field(default=None, max_length=64)
    milestone: str = Field(default="", max_length=64)
    summary: str = Field(default="", max_length=512)


class MissionControlCursor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ingest_seq: int = Field(ge=0)


class MissionControlSnapshotView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"] = contract.CONTRACT_VERSION
    coverage: Literal["owner-runtime"] = contract.SNAPSHOT_COVERAGE
    generated_at: datetime
    cursor: MissionControlCursor | None = None

    owner: ValueLane[MissionControlOwner]
    companions: ItemsLane[MissionControlCompanion]
    devices: ItemsLane[MissionControlDevice]
    activities: ItemsLane[MissionControlActivity]
    turns: ItemsLane[MissionControlTurn]
    jobs: ItemsLane[MissionControlJob]
    memory: ValueLane[MissionControlMemory]
    services: ItemsLane[MissionControlService]
    events: ItemsLane[MissionControlEvent]


# ── projection ─────────────────────────────────────────────────────────────

# What this boundary cannot reach yet. Each lane says which capability is
# missing rather than arriving empty, because "no activities" and "no way to ask
# about activities" are answers to different questions.
_NO_ACTIVITY_PRODUCER = (
    "控制面还没有 Owner 活动投影：活动、对话轮次与后台任务读不到"
)
_NO_EVENT_PRODUCER = "控制面还没有 Owner 事件游标接口：事件读不到"
_NO_MEMORY_PRODUCER = "控制面还没有 Owner 记忆投影：记忆空间与后台整理读不到"
_ONLY_PRIMARY_COMPANION = (
    "控制面只提供主 Companion：这个 Owner 的其余伙伴没有读到"
)

# Tier is a fact about where a service sits in the architecture, not about its
# health, and the Local API's service inventory does not carry it. Unknown ids
# default to `service` — the conservative choice, since mistaking a core service
# for an optional extension is the more misleading direction.
_TIERS: Mapping[str, str] = {
    "hub": "service",
    "channel": "service",
    "agent": "service",
    "memory": "service",
    "data": "service",
    "livekit": "middleware",
    "nats": "middleware",
    "mementos": "external",
}


def _service_health(state: RuntimeState) -> tuple[bool, bool]:
    """(online, checked) for one host service.

    ``unknown`` is the state that matters: it means nobody could tell, and it
    must not become "healthy" on the way to a screen.
    """

    if state == "unknown":
        return False, False
    return state == "ready", True


def _source_detail(name: str, status: SourceStatus) -> str:
    """One authority's failure, in the words it used.

    Restated rather than summarised: "Hub: unavailable" is actionable and
    "degraded" is not.
    """

    if status.state == "ok":
        return ""
    failure = status.failure
    if failure is None:
        return f"{name}：没有回答"
    return f"{name}：{failure.kind} · {failure.detail}"


def _presence_unknown() -> MissionControlPresence:
    """No authority answered, and this boundary has none to ask.

    Deliberately not a guess. Kernel proves the mount and Hub's directory
    proves the name; neither is a presence, and a projection that turned either
    into one would make an offline body look live.
    """

    return MissionControlPresence(
        state=contract.PRESENCE_UNKNOWN,
        source=contract.PRESENCE_SOURCE_NONE,
    )


def _device_view(
    mount: KernelMount,
    entry: HubDevice | None,
) -> MissionControlDevice:
    bound = mount.attached_companion_id
    return MissionControlDevice(
        device_id=mount.device_id,
        display_name=entry.display_name if entry else "",
        device_kind=entry.device_kind if entry else "",
        # The logical role comes from the companion a device is bound to, never
        # from its board kind. Without a companion there is no role to state.
        role="对话身体" if bound else "",
        role_kind="persona" if bound else "unbound",
        companion_id=bound,
        presence=_presence_unknown(),
    )


def owner_mission_control_view(
    *,
    bound_owner_id: str,
    owner_display_name: str | None,
    companion: MissionControlCompanion | None,
    companion_detail: str = "",
    inventory: OwnerInventory | None,
    inventory_detail: str = "",
    services: LocalHostServiceInventoryView | None,
    services_detail: str = "",
) -> MissionControlSnapshotView:
    """Assemble one Owner's snapshot from whatever each authority answered.

    Pure on purpose: every failure mode of this projection is a combination of
    which inputs arrived, and that is worth being able to enumerate in a test
    rather than to reproduce by taking services down.
    """

    owner_lane: ValueLane[MissionControlOwner]
    if owner_display_name is None:
        owner_lane = ValueLane[MissionControlOwner](
            state="degraded",
            detail="读不到主人的名字，只读到归属",
            observed_at=datetime.now(UTC),
            value=MissionControlOwner(owner_id=bound_owner_id),
        )
    else:
        owner_lane = ValueLane[MissionControlOwner](
            state="ok",
            observed_at=datetime.now(UTC),
            value=MissionControlOwner(
                owner_id=bound_owner_id,
                display_name=owner_display_name,
            ),
        )

    if companion is None:
        companions_lane = _missing_items(companion_detail or _ONLY_PRIMARY_COMPANION)
    else:
        # Read something, not everything: exactly what `degraded` is for.
        companions_lane = _ok_items(
            (companion,),
            state="degraded",
            detail=_ONLY_PRIMARY_COMPANION,
        )

    if inventory is None:
        devices_lane = _missing_items(
            inventory_detail or "设备权威不可用，读不到这个 Owner 的身体"
        )
    else:
        directory = {entry.device_id: entry for entry in inventory.devices}
        # Kernel keeps the mount record of a removed device, inactive, because
        # that record carries the revision the next admission swaps against. It
        # is not a membership, so it is not a body on this screen.
        devices = tuple(
            _device_view(mount, directory.get(mount.device_id))
            for mount in inventory.mounts
            if mount.active
        )
        detail_parts = [
            part
            for part in (
                _source_detail("Hub", inventory.hub),
                _source_detail("Kernel", inventory.kernel),
                "在场没有权威回答（这个边界读不到运行黑板与 Hub 在场）",
            )
            if part
        ]
        devices_lane = _ok_items(
            devices,
            # Presence unanswered is itself a partial read, so this lane never
            # claims `ok` — a client must not mistake "unprobed" for "probed and
            # offline", and the lane state is the first place it would look.
            state="degraded",
            detail="；".join(detail_parts),
        )

    if services is None:
        services_lane = _missing_items(
            services_detail or "读不到主机在跑什么"
        )
    else:
        rows = []
        for service in services.services:
            online, checked = _service_health(service.runtime_state)
            rows.append(
                MissionControlService(
                    service_id=service.service_id,
                    display_name=service.service_id,
                    code=service.service_id,
                    mode="托管",
                    tier=_TIERS.get(service.service_id, "service"),
                    online=online,
                    checked=checked,
                    detail=service.detail or "",
                )
            )
        services_lane = _ok_items(tuple(rows))

    return MissionControlSnapshotView(
        generated_at=datetime.now(UTC),
        # No events read, so nothing to resume from. A cursor invented here
        # would send a client back to a position that never existed.
        cursor=None,
        owner=owner_lane,
        companions=companions_lane,
        devices=devices_lane,
        activities=_missing_items(_NO_ACTIVITY_PRODUCER),
        turns=_missing_items(_NO_ACTIVITY_PRODUCER),
        jobs=_missing_items(_NO_ACTIVITY_PRODUCER),
        memory=ValueLane[MissionControlMemory](
            state="unavailable",
            detail=_NO_MEMORY_PRODUCER,
        ),
        services=services_lane,
        events=_missing_items(_NO_EVENT_PRODUCER),
    )
