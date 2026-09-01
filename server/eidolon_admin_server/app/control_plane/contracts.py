"""Strict consumed contracts and Admin-owned control-plane read models."""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar, Literal

from eidolon_sdk.biz.contracts.companion import CompanionLifecycleState
from eidolon_sdk.biz.contracts.refusal import Refusal, RefusalKind
from eidolon_sdk.device_foundation.v1 import (
    BusinessOwnerId,
    ClaimQuery,
    ClaimRecord,
    ControllerActorRef,
    DecideEnrollment,
    DecideEnrollmentResult,
    DeviceRef,
    EnrollmentProposalQuery,
    EnrollmentRecoveryProjection,
    OwnerDomainId,
)
from eidolon_sdk.device_foundation.v1 import (
    RevokeClaimResult as HubClaimRevocationResult,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from eidolon_admin_server.conversation_identity import CONVERSATION_ID_MAX_LENGTH


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ServiceEndpoint(StrictModel):
    operation: Literal["system.service-endpoint"]
    service_id: str = Field(min_length=1, max_length=128)
    endpoint_id: str = Field(min_length=1, max_length=128)
    protocol: str = Field(min_length=1, max_length=32)
    address: str = Field(min_length=1, max_length=2048)
    contract: str = Field(min_length=1, max_length=256)


class CompanionIdentity(StrictModel):
    """Data's published Companion identity, consumed strictly.

    Strict means a producer field this model does not name is a hard parse
    failure, which is the behaviour we want — and it is why this model must
    track the published schema exactly. It did not: Data grew ``kind`` and
    ``revision`` and split ``inactive`` into ``retiring``/``archived``, and
    every Admin read of a Companion broke until this line was updated.
    ``tests/test_data_contract_drift.py`` now checks the model against the
    producer's schema, so the next divergence fails in this suite instead of at
    runtime on a Host.
    """

    operation: Literal["companion.identity"]
    companion_id: str = Field(min_length=1, max_length=64)
    owner_id: str = Field(min_length=1, max_length=64)
    #: What the Owner calls this Eidolon. Defaulted so a Host whose Data
    #: predates answering with it still parses.
    display_name: str = Field(default="", max_length=128)
    #: Four states, not two. A consumer must be able to tell "the Owner
    #: archived it" from "it cannot run right now"; folding them was the
    #: conflation the identity schema was changed to remove.
    lifecycle_state: CompanionLifecycleState
    #: The product type (standard, guard, ...), independent of which Companion
    #: is the Owner's default. Deliberately not a Literal: this is a consumer,
    #: and a kind it has never heard of must not fail the parse of an identity
    #: it can otherwise read.
    kind: str = Field(min_length=1, max_length=32)
    #: Aggregate version, for compare-and-swap on writes.
    revision: int = Field(ge=1)


class CompanionSummary(StrictModel):
    """One row of an Owner's roster.

    Deliberately carries no "is the default" flag. That fact is one field on the
    Owner, and repeating it per row would make "two rows both claim it" a
    representable state — a second place adjudicating one fact. The page names
    the pointer once and a reader compares.
    """

    companion_id: str = Field(min_length=1, max_length=64)
    display_name: str = Field(default="", max_length=128)
    #: Plain string for the same reason as ``CompanionIdentity.kind``: the set
    #: of product types is the producer's to grow.
    kind: str = Field(min_length=1, max_length=32)
    lifecycle_state: CompanionLifecycleState
    revision: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime
    current_genome_id: str | None = Field(default=None, max_length=64)
    memory_realm_id: str | None = Field(default=None, max_length=64)


class MemoryRealmRuntime(StrictModel):
    spec: dict
    health: dict
    mcp_http_url: str


class MemoryRealmRuntimePage(StrictModel):
    realms: tuple[MemoryRealmRuntime, ...] = ()
    memory_available: bool


class CompanionRosterPage(StrictModel):
    """A page of one Owner's Companions, as the authority answers it."""

    contract_version: Literal["1"]
    operation: Literal["companion.roster-page"]
    owner_id: str = Field(min_length=1, max_length=64)
    #: Verbatim from the Owner aggregate. ``None`` is a real state and nothing
    #: above may resolve it by picking a row.
    default_companion_id: str | None = Field(default=None, max_length=64)
    companions: tuple[CompanionSummary, ...] = ()
    #: Opaque both ways: consumed as received, forwarded as received. Reading it
    #: would make the producer's page boundary part of this contract.
    next_cursor: str | None = Field(default=None, max_length=256)


class ProvisionedCompanion(StrictModel):
    companion_id: str = Field(min_length=1, max_length=64)
    display_name: str = Field(default="", max_length=128)
    kind: str = Field(min_length=1, max_length=32)
    lifecycle_state: CompanionLifecycleState
    revision: int = Field(ge=1)


class CompanionProvision(StrictModel):
    """What one create produced, identically on a retry."""

    contract_version: Literal["1"]
    operation: Literal["companion.provision"]
    operation_id: str = Field(min_length=1, max_length=64)
    request_fingerprint: str = Field(min_length=1, max_length=128)
    companion: ProvisionedCompanion
    persona_genome_id: str = Field(min_length=1, max_length=64)
    memory_realm_id: str = Field(min_length=1, max_length=64)
    #: The one field that changes what the caller does next: a realm that was
    #: just catalogued has no running process yet, and one that already existed
    #: needs nothing.
    memory_realm_created: bool
    replayed: bool


class CompanionLifecycleResult(StrictModel):
    """Where a Companion ended up, and who answers for the Owner now.

    ``default_companion_id`` is part of the answer because retiring the
    Companion an Owner's unaddressed work goes to moves the pointer in the same
    transaction. A caller that had to read it back would be asking a second
    question about something this one already settled — and would be reading it
    from a moment that is no longer the moment it changed.
    """

    operation: Literal["companion.lifecycle"]
    companion_id: str = Field(min_length=1, max_length=64)
    lifecycle_state: CompanionLifecycleState
    revision: int = Field(ge=1)
    default_companion_id: str | None = Field(default=None, max_length=64)


class GovernanceEvent(StrictModel):
    """One governance fact, consumed as the authority publishes it."""

    event_id: str = Field(min_length=1, max_length=64)
    action: str = Field(min_length=1, max_length=128)
    subject_type: str = Field(min_length=1, max_length=64)
    subject_id: str = Field(min_length=1, max_length=128)
    outcome: str = Field(min_length=1, max_length=16)
    severity: str = Field(min_length=1, max_length=16)
    occurred_at: str = Field(min_length=1, max_length=64)
    payload: dict[str, Any] = Field(default_factory=dict)


class OwnerGovernanceEvents(StrictModel):
    contract_version: Literal["1"]
    operation: Literal["owner.governance-events"]
    owner_id: str = Field(min_length=1, max_length=64)
    events: tuple[GovernanceEvent, ...] = ()
    next_cursor: int | None = None


class MemoryRoom(StrictModel):
    room_id: str = Field(min_length=1, max_length=256)
    drawer_count: int = Field(ge=0)
    #: A few titles, enough to recognise the room. Not the contents — a browse
    #: that returned everything would be an export by another name.
    drawers_preview: tuple[dict[str, Any], ...] = ()
    preview_truncated: bool = False


class MemoryWing(StrictModel):
    wing_id: str = Field(min_length=1, max_length=128)
    is_configured: bool
    display_name: str = Field(default="", max_length=256)
    description: str = Field(default="", max_length=2048)
    sort_order: int
    room_count: int = Field(ge=0)
    drawer_count: int = Field(ge=0)
    rooms: tuple[MemoryRoom, ...] = ()


class MemoryMaterialization(StrictModel):
    ready: bool
    data_readable: bool
    materialization_state: Literal["ready", "materializing", "degraded", "unavailable"]
    projection_pending: int = Field(ge=0)
    last_materialized_at: str | None = Field(default=None, max_length=64)
    degraded_reason: str = Field(default="", max_length=1024)


class MemoryStatus(StrictModel):
    contract_version: Literal["1"]
    operation: Literal["memory.status"]
    memory_realm_id: str = Field(min_length=1, max_length=64)
    memory_space_id: str = Field(min_length=1, max_length=64)
    audience_scope: str = Field(min_length=1, max_length=128)
    ready: bool
    data_readable: bool
    materialization_state: Literal["ready", "materializing", "degraded", "unavailable"]
    projection_pending: int = Field(ge=0)
    last_materialized_at: str | None = Field(default=None, max_length=64)
    degraded_reason: str = Field(default="", max_length=1024)

    def materialization(self) -> MemoryMaterialization:
        return MemoryMaterialization(
            ready=self.ready,
            data_readable=self.data_readable,
            materialization_state=self.materialization_state,
            projection_pending=self.projection_pending,
            last_materialized_at=self.last_materialized_at,
            degraded_reason=self.degraded_reason,
        )


class MemoryBrowse(StrictModel):
    """What an Owner's memory holds, by wing and room."""

    contract_version: Literal["1"]
    operation: Literal["memory.browse"]
    memory_space_id: str = Field(min_length=1, max_length=64)
    audience_scope: str = Field(min_length=1, max_length=128)
    materialization: MemoryMaterialization
    wings: tuple[MemoryWing, ...] = ()
    entry_count: int = Field(ge=0)
    #: Present and not listed — the Owner's own privacy wing is the common case.
    #: Carried through rather than dropped: a count that disagrees with what is
    #: listed is worse than a count that explains itself.
    withheld_count: int = Field(ge=0)
    truncated: bool


class MemoryGraphNode(StrictModel):
    node_id: str = Field(min_length=1, max_length=256)
    label: str = Field(min_length=1, max_length=256)
    degree: int = Field(ge=0)


class MemoryGraphEdge(StrictModel):
    edge_id: str = Field(min_length=1, max_length=128)
    subject: str = Field(min_length=1, max_length=256)
    predicate: str = Field(min_length=1, max_length=128)
    object: str = Field(min_length=1, max_length=256)
    confidence: float = Field(ge=0.0, le=1.0)
    recorded_at: str = Field(default="", max_length=64)


class MemoryGraph(StrictModel):
    contract_version: Literal["1"]
    operation: Literal["memory.graph"]
    memory_space_id: str = Field(min_length=1, max_length=64)
    nodes: tuple[MemoryGraphNode, ...] = ()
    edges: tuple[MemoryGraphEdge, ...] = ()
    truncated: bool


class MemoryEntry(StrictModel):
    entry_id: str = Field(min_length=1, max_length=128)
    recorded_at: str = Field(min_length=1, max_length=64)
    #: Which field the time came from. Carried because "it filed this under
    #: yesterday" is a real complaint and this is what makes it answerable.
    recorded_at_source: str = Field(default="", max_length=64)
    wing_id: str = Field(default="", max_length=128)
    room_id: str = Field(default="", max_length=256)
    preview: str = Field(default="", max_length=4096)


class MemoryEntries(StrictModel):
    """What was recorded at or after an instant the caller named."""

    contract_version: Literal["1"]
    operation: Literal["memory.entries"]
    memory_space_id: str = Field(min_length=1, max_length=64)
    since: str = Field(min_length=1, max_length=64)
    entries: tuple[MemoryEntry, ...] = ()
    entry_count: int = Field(ge=0)
    #: The page ended inside the window. Distinct from ``truncated``, which is
    #: the palace scan stopping — one is about this answer, the other about how
    #: much of the memory was looked at.
    more_in_window: bool
    #: Visible and holding no usable time, so in no day's list. Relayed rather
    #: than dropped: a person whose entry never appears should be able to learn
    #: that this is why.
    undated_count: int = Field(ge=0)
    truncated: bool


class MemoryExportRecord(StrictModel):
    """One memory, whole.

    ``value`` rather than ``preview``: the day list and the library shorten what
    they show because someone is scrolling them, and this is the copy a person
    keeps. A preview here would be data loss that looks like a working read.
    """

    entry_id: str = Field(min_length=1, max_length=128)
    #: Empty when the record carries no derivable time. Those are in the file, at
    #: the end, rather than omitted — leaving one out of a copy is losing it.
    recorded_at: str = Field(default="", max_length=64)
    recorded_at_source: str = Field(default="", max_length=64)
    wing_id: str = Field(default="", max_length=128)
    room_id: str = Field(default="", max_length=256)
    memory_type: str = Field(default="", max_length=64)
    #: Who was told. Memory added this when the physical Owner Realm replaced
    #: per-Companion realms: an Owner export may now contain several logical
    #: audiences, and accepting the field is required to keep that copy honest.
    #: Defaulted for an older Memory realm whose export predates the audience
    #: axis; those records live in the Owner layer by definition.
    audience: str = Field(default="owner", min_length=1, max_length=192)
    #: Required rather than defaulted, unlike every other field here: this is
    #: what the copy is *of*. A record whose text may be absent would let a file
    #: validate while carrying nothing a person could read.
    value: str = Field(max_length=65536)


class MemoryExport(StrictModel):
    """A copy of an Owner's memory, in a form they can read and keep.

    Not the Host backup. That copy is the palace — vectors, ledgers, the encoder
    they were built under — and exists so a lost disk is survivable; it is taken
    by the operator tool and never passes through here. This one exists so a
    person is not locked in, and the two share nothing but the word "export".
    """

    contract_version: Literal["1"]
    operation: Literal["memory.export"]
    memory_space_id: str = Field(min_length=1, max_length=64)
    #: Two exports of the same memory differ, and a file with no instant cannot
    #: be told apart from a stale one.
    taken_at: str = Field(min_length=1, max_length=64)
    records: tuple[MemoryExportRecord, ...] = ()
    record_count: int = Field(ge=0)
    undated_count: int = Field(ge=0)
    #: The palace scan stopped before the end. What is here is real; it is not
    #: all of it, and a file that said nothing about that would be worse.
    truncated: bool


class ForgetCandidate(StrictModel):
    drawer_id: str = Field(min_length=1, max_length=128)
    score: float = Field(ge=0.0, le=1.0)
    preview: str = Field(default="", max_length=4096)


class ForgetPreview(StrictModel):
    """What "forget this" would remove, and the token that binds it.

    ``status`` is the realm's word for what it found. Carried rather than
    flattened into success or failure: "nothing matched" and "too many matched"
    lead a person to different next steps, and a client that saw only an empty
    list could not tell them apart.
    """

    contract_version: Literal["1"]
    operation: Literal["memory.forget-preview"]
    status: Literal["preview", "not_found", "too_broad"]
    target: str = Field(min_length=1, max_length=512)
    action: Literal["archive", "delete"] | None = None
    entries: tuple[ForgetCandidate, ...] = ()
    needs_confirmation: bool = False
    #: Opaque, and signed by the realm that minted it. Nothing above the realm
    #: parses it: the whole point is that the confirm acts on what the preview
    #: bound, and a layer that could read it could also build one.
    confirmation_token: str | None = Field(default=None, max_length=4096)
    expires_at: int | None = None
    #: Present when the realm refused to resolve — why it was too broad.
    detail: str = Field(default="", max_length=1024)


class ForgetOutcome(StrictModel):
    """What became of a confirmed forget.

    ``extra="allow"`` here alone: the realm merges the command ledger's own
    status dictionary into this answer, and that vocabulary belongs to the
    ledger rather than to this contract. Pinning it would make every ledger
    field an Admin release.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    contract_version: Literal["1"]
    operation: Literal["memory.forget-confirm"]
    action: Literal["archive", "delete"]
    target: str = Field(min_length=1, max_length=512)
    entry_count: int = Field(ge=0)
    #: The ledger's word, relayed. Publishing is durable and applying is a
    #: projection that may still be running, so "done" is not this layer's to
    #: decide.
    status: str = Field(min_length=1, max_length=64)




class ConsumedModel(BaseModel):
    """A producer's answer, read narrowly on purpose.

    Every other consumed model here is strict — an unknown field is a parse
    failure, which is what we want when the producer publishes a contract this
    surface mirrors field for field. The Agent's admin API is different: it is
    also a debugging surface, and its rows carry thirty-odd fields of latency,
    tokens, model names, worker leases and provider payloads. Refusing to parse
    because it grew a new debug field would take down a person's page for a
    reason that has nothing to do with them.

    So this one ignores what it was not asked for. The narrowing *is* the
    projection: what a person sees is chosen here, not by whatever the runtime
    happened to record.
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class RuntimeSessionRevocation(ConsumedModel):
    """What the runtime did when asked to sign every device out."""

    owner_id: str = Field(min_length=1, max_length=64)
    revoked: bool
    #: The instant everything issued before it stopped being valid. Carried
    #: because it *is* the mechanism — a token minted after it works — so a
    #: person can be told when, and a client can tell "signed out at 21:04" from
    #: "signed out, apparently forever".
    revoked_at: str = Field(min_length=1, max_length=64)


class RuntimeCompanionRow(ConsumedModel):
    """One Companion this Host currently has a live runtime for."""

    companion_id: str = Field(min_length=1, max_length=64)
    genome_id: str = Field(default="", max_length=64)
    started_at: str = Field(default="", max_length=64)
    #: When anything last addressed it. Carried because "started" alone cannot
    #: tell a Companion used a minute ago from one resolved at boot and left.
    last_active_at: str = Field(default="", max_length=64)


class OwnerRuntimeCompanions(ConsumedModel):
    """Which of this Owner's Companions are live, as the runtime says.

    Several at once is the ordinary case (plan §4.6). Consumers used to infer
    "which one is running" from whether the Owner had a default — a routing
    fallback — which made the answer both wrong and singular.

    Not presence. A Companion here is one this Host can run, not one anybody can
    currently reach; nothing on this Host tracks whether a body is connected.
    """

    owner_id: str = Field(min_length=1, max_length=64)
    companions: tuple[RuntimeCompanionRow, ...] = ()


class ConversationRow(ConsumedModel):
    """One conversation, as the runtime holds it."""

    conversation_id: str = Field(min_length=1, max_length=CONVERSATION_ID_MAX_LENGTH)
    owner_id: str = Field(min_length=1, max_length=64)
    companion_id: str = Field(default="", max_length=64)
    title: str | None = Field(default=None, max_length=512)
    status: str = Field(default="", max_length=32)
    started_at: str | None = Field(default=None, max_length=64)
    updated_at: str | None = Field(default=None, max_length=64)
    ended_at: str | None = Field(default=None, max_length=64)


class ConversationRows(ConsumedModel):
    conversations: tuple[ConversationRow, ...] = ()
    #: The runtime's keyset cursor — an instant, passed back untouched. Read by
    #: nothing here: what it means is the producer's business.
    next_before: str | None = Field(default=None, max_length=64)


class MessageRow(ConsumedModel):
    """One thing said in a turn, as the runtime holds it."""

    role: str = Field(default="", max_length=32)
    content: str = Field(default="", max_length=1_048_576)
    content_type: str = Field(default="", max_length=64)
    #: Present on tool traffic. Consumed so the projection can tell tool messages
    #: apart from what a person said, and dropped there rather than here.
    tool_name: str | None = Field(default=None, max_length=128)
    created_at: str | None = Field(default=None, max_length=64)


class TranscriptTurnRow(ConsumedModel):
    turn_id: str = Field(min_length=1, max_length=64)
    seq: int
    started_at: str | None = Field(default=None, max_length=64)
    finished_at: str | None = Field(default=None, max_length=64)
    status: str = Field(default="", max_length=32)
    messages: tuple[MessageRow, ...] = ()


class TranscriptRows(ConsumedModel):
    owner_id: str = Field(min_length=1, max_length=64)
    companion_id: str = Field(min_length=1, max_length=64)
    conversation_id: str = Field(min_length=1, max_length=CONVERSATION_ID_MAX_LENGTH)
    turns: tuple[TranscriptTurnRow, ...] = ()
    next_before: str | None = Field(default=None, max_length=64)


class TaskRow(ConsumedModel):
    """One long task, as the runtime holds it.

    ``status`` is a plain string rather than a Literal deliberately. The
    vocabulary is the Agent's, it has nine values today, and a consumer that
    refused an unfamiliar one would turn a runtime that grew a state into a page
    that cannot be opened.
    """

    task_id: str = Field(min_length=1, max_length=64)
    owner_id: str = Field(min_length=1, max_length=64)
    companion_id: str = Field(default="", max_length=64)
    status: str = Field(min_length=1, max_length=32)
    task: str = Field(default="", max_length=8192)
    task_type: str = Field(default="", max_length=64)
    urgency: str = Field(default="", max_length=32)
    expected_output: str | None = Field(default=None, max_length=4096)
    progress_summary: str | None = Field(default=None, max_length=8192)
    result_text: str | None = Field(default=None, max_length=65536)
    error_code: str | None = Field(default=None, max_length=128)
    error_message: str | None = Field(default=None, max_length=4096)
    created_at: str | None = Field(default=None, max_length=64)
    updated_at: str | None = Field(default=None, max_length=64)
    completed_at: str | None = Field(default=None, max_length=64)


class TaskRows(ConsumedModel):
    tasks: tuple[TaskRow, ...] = ()
    next_before: str | None = Field(default=None, max_length=64)


class PersonaChapter(StrictModel):
    genome_id: str = Field(min_length=1, max_length=64)
    version: int = Field(ge=1)
    lifecycle_state: Literal["committed", "proposed", "rejected", "stale"]
    change_summary: str = Field(default="", max_length=4096)
    restored_from_version: int | None = None
    is_current: bool = False
    created_at: str


class PersonaTimeline(StrictModel):
    operation: Literal["companion.persona-timeline"]
    companion_id: str = Field(min_length=1, max_length=64)
    chapters: tuple[PersonaChapter, ...] = ()


class PersonaRestoreRequest(StrictModel):
    genome_id: str = Field(min_length=1, max_length=64)
    change_summary: str = Field(default="", max_length=4096)


class DeviceRenameCommand(StrictModel):
    display_name: str = Field(min_length=1, max_length=128)


class CompanionRenameRequest(StrictModel):
    display_name: str = Field(min_length=1, max_length=128)


class CompanionFace(StrictModel):
    """Whether this Companion has a face, and which one — never the face."""

    operation: Literal["companion.face"]
    companion_id: str = Field(min_length=1, max_length=64)
    has_face: bool
    face_asset_id: str | None = None
    sha256: str | None = None
    size_bytes: int | None = None
    updated_at: str | None = None


class OwnerIdentity(StrictModel):
    operation: Literal["owner.identity"]
    owner_id: str = Field(min_length=1, max_length=64)
    #: What this person is called. Given at first use; correctable since.
    display_name: str = Field(default="", max_length=128)
    lifecycle_state: Literal["active", "archived", "deleting"]
    #: Which Companion answers when nothing named one. Read here, from the Owner
    #: aggregate that owns it, rather than derived by scanning a Companion list —
    #: one field, one answer. ``None`` is real: no default-eligible Companion,
    #: and this layer must not resolve it by choosing one.
    default_companion_id: str | None = Field(default=None, max_length=64)
    #: Owner aggregate version, for the If-Match a writer sends.
    revision: int = Field(ge=1)


class OwnerRenameRequest(StrictModel):
    display_name: str = Field(min_length=1, max_length=128)


class WorkspaceInitializeRequest(StrictModel):
    owner_display_name: str = Field(min_length=1, max_length=128)
    companion_display_name: str = Field(default="Eidolon", min_length=1, max_length=128)


class WorkspaceOwner(StrictModel):
    owner_id: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=128)
    lifecycle_state: Literal["active"]


class WorkspaceResources(StrictModel):
    state: Literal["ready"]
    primary_companion_id: str = Field(min_length=1, max_length=64)
    persona_genome_id: str = Field(min_length=1, max_length=64)
    memory_realm_id: str = Field(min_length=1, max_length=64)


class WorkspaceOperation(StrictModel):
    contract_version: Literal["1"]
    operation: Literal["owner-workspace.initialize"]
    operation_id: str = Field(
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    )
    request_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    status: Literal["succeeded"]
    owner: WorkspaceOwner
    workspace: WorkspaceResources


class KernelMount(StrictModel):
    """Whether a device belongs to this Owner's Host, and at which generation.

    It no longer says which Eidolon answers through it. That was a field here
    once, which meant a device being re-claimed silently forgot its Eidolon and
    two Controllers changing unrelated things about one device collided; it is a
    Body assignment with its own revision now.
    """

    operation: Literal["kernel.device-mount"]
    device_id: str = Field(min_length=1, max_length=128)
    owner_id: str = Field(min_length=1, max_length=64)
    device_ref: DeviceRef
    revision: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime
    request_id: str = Field(min_length=1, max_length=96)
    fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    active: bool


class KernelMountPage(StrictModel):
    operation: Literal["kernel.device-mount-page"]
    next_cursor: str | None = Field(default=None, max_length=128)
    mounts: tuple[KernelMount, ...] = Field(default=(), max_length=100)

    @field_validator("mounts", mode="before")
    @classmethod
    def _array(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value


class KernelBodyAssignment(StrictModel):
    """Which Companion answers through one Body, and why it says so.

    ``selection_provenance`` is what lets a screen tell "you cleared this" apart
    from "the Eidolon it answered as was put away". Both leave the same
    ``companion_id: null`` behind, and a speaker that went quiet without a
    sentence is indistinguishable from a broken one.
    """

    operation: Literal["kernel.body-assignment"]
    assignment_id: str = Field(min_length=1, max_length=160)
    body_endpoint_id: str = Field(min_length=1, max_length=128)
    device_id: str = Field(min_length=1, max_length=128)
    endpoint_id: str = Field(min_length=1, max_length=64)
    owner_id: str = Field(min_length=1, max_length=64)
    companion_id: str | None = Field(default=None, min_length=1, max_length=64)
    selection_provenance: Literal[
        "user_selected", "user_cleared", "companion_deleted", "policy_reconciled"
    ]
    change_reason: str | None = Field(default=None, min_length=1, max_length=256)
    mode: Literal["default"]
    policy_refs: tuple[str, ...] = Field(default=(), max_length=16)
    revision: int = Field(ge=1)
    generation: int = Field(ge=1)
    updated_at: datetime
    status: dict[str, Any]

    @field_validator("policy_refs", mode="before")
    @classmethod
    def _array(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @property
    def effective_companion_id(self) -> str | None:
        """Who is actually answering, as the authority reports it.

        Not ``companion_id``: a Body whose device is gone keeps its assignment
        on purpose, and the status is where the authority says whether it is in
        force.
        """

        value = self.status.get("effective_companion_id")
        return value if isinstance(value, str) and value else None


class KernelBodyEndpoint(StrictModel):
    operation: Literal["kernel.body-endpoint"]
    body_endpoint_id: str = Field(min_length=1, max_length=128)
    device_id: str = Field(min_length=1, max_length=128)
    owner_id: str = Field(min_length=1, max_length=64)
    endpoint_id: str = Field(min_length=1, max_length=64)
    device_ref: DeviceRef
    mount_revision: int = Field(ge=1)
    roles: tuple[str, ...] = Field(default=(), max_length=8)
    assignment_policy: Literal["required", "optional", "forbidden"]
    risk_class: Literal["safe", "sensitive", "hazardous"]
    concurrency: Literal["shared", "exclusive", "leased"]
    source: Literal["derived", "manifest"]
    present: bool
    assignment: KernelBodyAssignment | None = None

    @field_validator("roles", mode="before")
    @classmethod
    def _array(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @property
    def assignment_revision(self) -> int:
        """What a change has to carry. Zero for a Body nobody has decided about."""

        return 0 if self.assignment is None else self.assignment.revision


class KernelBodyEndpointPage(StrictModel):
    operation: Literal["kernel.body-endpoint-page"]
    endpoints: tuple[KernelBodyEndpoint, ...] = Field(default=(), max_length=100)

    @field_validator("endpoints", mode="before")
    @classmethod
    def _array(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value


class KernelMutationResult(StrictModel):
    operation: Literal["kernel.device-mount-mutation-result"]
    mount: KernelMount
    audit_position: int = Field(ge=1)
    replayed: bool


class OperatorDeviceAdmissionRequest(StrictModel):
    """One explicit operator action from the Admin Web control-plane page.

    Hub still owns admission and Kernel still owns mounts. This is only the
    input needed by Admin to orchestrate those current public contracts.
    """

    request_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._:-]+$")
    owner_id: BusinessOwnerId
    device_id: str = Field(
        min_length=3, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"
    )
    companion_id: str | None = Field(default=None, min_length=1, max_length=64)
    expected_mount_revision: int = Field(default=0, ge=0, strict=True)
    replace_existing_mount: bool = False


class ControllerCommand(StrictModel):
    """A canonical command carried under the authority a Controller holds.

    Every command here answers the same two questions — is the actor acting in
    the Owner Domain the command targets, and does it hold what the command
    requires — so both are asked once, here, rather than restated per command.

    They used to be restated. Three copies agreed, which is what a rule looks
    like right up until one of them does not: reads were minted carrying
    `device.read` alone while only the Decision carried `device.claim.approve`.
    That describes the request rather than the principal, and the pending-device
    queue, which only an approver may read, answered 403 on the Host while every
    test passed.
    """

    contract_version: Literal["1"]
    actor: ControllerActorRef

    #: What this command requires of whoever issues it. Declared by the command,
    #: because it is a property of the command and not of any one call site.
    required_scope: ClassVar[str]

    def target_owner_domain_id(self) -> OwnerDomainId:
        raise NotImplementedError

    @model_validator(mode="after")
    def _authority(self) -> "ControllerCommand":
        name = type(self).__name__
        if self.actor.owner_domain_id != self.target_owner_domain_id():
            raise ValueError(f"{name} actor and target Owner Domain do not match")
        if self.required_scope not in self.actor.granted_scopes:
            raise ValueError(f"{name} actor lacks {self.required_scope}")
        return self


class ControllerEnrollmentQuery(ControllerCommand):
    """Authenticated Local context wrapped around the canonical Hub query."""

    required_scope: ClassVar[str] = "device.read"

    business_owner_id: BusinessOwnerId
    query: EnrollmentProposalQuery

    def target_owner_domain_id(self) -> OwnerDomainId:
        return self.query.owner_domain_id


class ControllerCommissioningVoucherRequest(ControllerCommand):
    """Ask this Host to sign the standing a Body needs to be admitted.

    The device presents whatever base identity it has stored, if any; it does
    not get to keep it by saying so. The Host asks Hub whether it issued that
    identity to exactly this operational key and mints a new one otherwise, so
    a value a device chose for itself can never be signed into permanent
    history.
    """

    required_scope: ClassVar[str] = "device.claim.approve"

    business_owner_id: BusinessOwnerId
    owner_domain_id: OwnerDomainId
    #: The key the voucher binds to, as the device's own setup descriptor states
    #: it. Not the key itself: the Controller never holds the device's key, and
    #: the only thing the binding needs is which key it is.
    operational_spki_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    presented_device_base_id: str | None = None

    def target_owner_domain_id(self) -> OwnerDomainId:
        return self.owner_domain_id


class CommissioningVoucherIssued(StrictModel):
    contract_version: Literal["1"] = "1"
    voucher: str
    jti: str
    device_base_id: str
    expires_at: datetime


class ControllerEnrollmentRecoveryQuery(ControllerCommand):
    """One Enrollment's projection, read in the Controller's own Owner Domain.

    A page query answers "what is waiting"; this answers "what happened to the
    one I am setting up". Keeping it a separate command means the single-resource
    read cannot be reached by widening a page query's scope.
    """

    required_scope: ClassVar[str] = "device.read"

    business_owner_id: BusinessOwnerId
    owner_domain_id: OwnerDomainId
    enrollment_id: str = Field(
        min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$"
    )

    def target_owner_domain_id(self) -> OwnerDomainId:
        return self.owner_domain_id


class ControllerClaimQuery(ControllerCommand):
    required_scope: ClassVar[str] = "device.read"

    business_owner_id: BusinessOwnerId
    query: ClaimQuery

    def target_owner_domain_id(self) -> OwnerDomainId:
        return self.query.owner_domain_id


class ControllerEnrollmentDecisionIntent(ControllerCommand):
    """Admin workflow input; the Decision itself is the SDK binding."""

    required_scope: ClassVar[str] = "device.claim.approve"

    request_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    decision: DecideEnrollment

    def target_owner_domain_id(self) -> OwnerDomainId:
        return self.decision.target_owner_domain_id


class AdmissionDecisionWorkflowResult(StrictModel):
    """Admin's checkpoint plus Hub's canonical, owner-scoped projection."""

    operation: Literal["admin.admission-decision-intent"] = (
        "admin.admission-decision-intent"
    )
    request_id: str = Field(min_length=1, max_length=128)
    intent_id: str = Field(pattern=r"^admission-intent-[0-9a-f]{32}$")
    command_id: str = Field(pattern=r"^decide-enrollment-[0-9a-f]{32}$")
    checkpoint: Literal["intent_recorded", "decision_committed"]
    decision_result: DecideEnrollmentResult | None = None
    recovery: EnrollmentRecoveryProjection

    @model_validator(mode="after")
    def _coherent(self) -> "AdmissionDecisionWorkflowResult":
        if (self.checkpoint == "decision_committed") != (
            self.decision_result is not None
        ):
            raise ValueError("Decision checkpoint and result disagree")
        return self


class ControllerBodyAssignment(StrictModel):
    """Which Companion answers through one device, or none.

    Choosing and clearing are the same decision with two values, so they are one
    command: `companion_id = None` is "nothing answers through it". The expected
    revision is the Owner's compare-and-swap over the *assignment* — zero for a
    Body nobody has decided about yet, which is why the bound is zero and not
    one.

    ``origin`` says which act this is part of, and it is set by the Admin-side
    caller rather than by any client: the Owner choosing on a device, or the
    archive workflow letting a Body go because the Eidolon it answered as is
    being put away. The Kernel derives the recorded provenance from it, so no
    caller can assert why something happened.
    """

    contract_version: Literal["1"]
    request_id: str = Field(min_length=1, max_length=96, pattern=r"^[A-Za-z0-9._:-]+$")
    owner_id: str = Field(min_length=1, max_length=64)
    device_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    companion_id: str | None = Field(default=None, min_length=1, max_length=64)
    expected_assignment_revision: int = Field(ge=0)
    origin: Literal["owner", "companion-lifecycle"] = "owner"
    change_reason: str | None = Field(default=None, min_length=1, max_length=256)


class ControllerDeviceRemovalRequest(StrictModel):
    """Internal service input derived from explicit Controller confirmation."""

    contract_version: Literal["1"]
    request_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    owner_id: str = Field(min_length=1, max_length=64)
    controller_id: str = Field(pattern=r"^ectrl-[0-9a-f]{20}$")
    device_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    reason: str = Field(default="owner-removed", min_length=1, max_length=128)


#: Every bounded context this process calls out to and can be refused by.
#:
#: Declared once and imported by the exception that carries a refusal
#: (``errors.AuthorityFailure``) as well as by the model that puts it on a wire
#: (``WorkflowFailure``). It used to be written twice, and the second copy is
#: what made a whole authority unreportable: ``agent`` was added to the client
#: that raises and not to the model that serialises, so every Agent refusal —
#: a missing credential, a runtime that is down — died inside the error path and
#: reached callers as an unexplained 500. One name, two readers: a vocabulary
#: that cannot be half-extended.
Authority = Literal["directory", "data", "hub", "kernel", "memory", "agent"]

#: How a refusal is to be *treated*, as distinct from what happened in the
#: domain (that is ``WorkflowFailure.code``). Same single-declaration rule as
#: ``Authority`` and for the same reason.
FailureKind = Literal[
    "unauthorized",
    "forbidden",
    "not_found",
    "conflict",
    "invalid_request",
    "unavailable",
    #: The authority answers, but the specific Realm/instance the request needs
    #: is not running. Distinct from ``unavailable`` because the next action
    #: differs: nothing is wrong with Memory, this one space has to be brought
    #: up. Reporting it as ``unavailable`` sent people to look at the wrong
    #: service.
    "runtime_missing",
    "upstream_failure",
    "contract_violation",
    #: This Host was never given the credential the call needs. Not retryable
    #: and not a fault of the authority: nothing about the request or the
    #: upstream service will change until somebody configures this Host.
    "configuration",
]


#: How each internal kind reads to somebody holding a phone.
#:
#: Declared here, in the process that owns the internal taxonomy, and sent down
#: the internal wire — *not* worked out again at the LAN boundary. That boundary
#: deliberately imports nothing from this half (it is the process that holds no
#: authority credential), so a table over there would be a second copy of a
#: vocabulary it cannot see change. Producing the projection here means a new
#: ``FailureKind`` fails this repository's own test until somebody decides what
#: it means to a person, which is the only moment that decision is cheap.
#:
#: The two vocabularies are deliberately different sizes. ``contract_violation``
#: and ``upstream_failure`` are one thing to a person and two to an operator;
#: ``configuration`` and ``unavailable`` are one thing to an operator — "go look
#: at the Host" — and two to a person, because only one of them is worth waiting
#: out.
PUBLIC_REFUSAL_KIND: dict[str, RefusalKind] = {
    "unauthorized": "denied",
    "forbidden": "denied",
    "not_found": "not_found",
    "conflict": "conflict",
    "invalid_request": "invalid",
    "unavailable": "not_running",
    "runtime_missing": "not_running",
    "upstream_failure": "upstream",
    "contract_violation": "upstream",
    "configuration": "not_configured",
}


class WorkflowFailure(StrictModel):
    authority: Authority
    kind: FailureKind
    detail: str
    #: The same refusal in the words a client acts on, projected here rather
    #: than at the boundary that publishes it. Everything above this line is for
    #: an operator reading a journal; this is for whoever is holding the phone,
    #: and the LAN process relays it without interpreting it.
    refusal: Refusal
    #: The upstream authority's refusal code, when it gave one — the domain word
    #: (``default_replacement_required``, ``revision_stale``), not the transport
    #: one. Optional because most authorities answer with a sentence only, and a
    #: consumer must treat its absence as "no further detail" rather than as a
    #: kind of failure.
    code: str | None = None
    upstream_status: int | None = None
    retryable: bool


class WorkflowStep(StrictModel):
    name: Literal[
        "hub_approval",
        "kernel_mount",
        "body_assignment",
        "hub_revocation",
    ]
    state: Literal["committed", "replayed", "failed", "not_requested", "not_attempted"]
    request_id: str | None = None
    revision: int | None = None
    failure: WorkflowFailure | None = None


class SourceStatus(StrictModel):
    state: Literal["ok", "error"]
    latency_ms: float = Field(ge=0)
    failure: WorkflowFailure | None = None


class OperatorOwnerDeviceInventory(StrictModel):
    """Current Hub Claims and Kernel mounts, composed for the Admin Web."""

    operation: Literal["admin.operator-device-inventory"] = (
        "admin.operator-device-inventory"
    )
    owner_id: BusinessOwnerId
    degraded: bool
    hub: SourceStatus
    kernel: SourceStatus
    claims: tuple[ClaimRecord, ...]
    mounts: tuple[KernelMount, ...]
    body_endpoints: tuple[KernelBodyEndpoint, ...] = ()


class OperatorDeviceAdmissionResult(StrictModel):
    """Progress across current Hub Admission and Kernel Mount authorities."""

    operation: Literal["admin.operator-device-admission"] = (
        "admin.operator-device-admission"
    )
    request_id: str = Field(min_length=1, max_length=64)
    outcome: Literal["completed", "retry_required", "blocked"]
    completed_stage: Literal[
        "received", "hub_approved", "kernel_mounted", "body_assigned"
    ]
    distributed_atomic: Literal[False] = False
    compensation: Literal["none-safe-intermediate"] = "none-safe-intermediate"
    recovery: Literal[
        "none", "retry-forward-same-request-id", "operator-action-required"
    ] = "none"
    steps: tuple[WorkflowStep, ...]
    mount: KernelMount | None = None


class DeviceRemovalResult(StrictModel):
    """A durable intent result plus observations from independent authorities."""

    operation: Literal["admin.device-removal-workflow"] = (
        "admin.device-removal-workflow"
    )
    request_id: str
    intent_id: str = Field(min_length=1, max_length=128)
    device_ref: DeviceRef
    outcome: Literal["completed", "accepted", "blocked"]
    completed_stage: Literal["received", "claim_revoked", "converged"]
    distributed_atomic: Literal[False] = False
    compensation: Literal["none-safe-intermediate"] = "none-safe-intermediate"
    recovery: Literal[
        "none", "retry-forward-same-request-id", "operator-action-required"
    ] = "none"
    steps: tuple[WorkflowStep, ...]
    hub: HubClaimRevocationResult | None = None
    conditions: tuple["RemovalCondition", ...] = ()


class RemovalCondition(StrictModel):
    name: Literal[
        "platform_access_revoked",
        "mount_removed",
        "device_erase_acknowledged",
    ]
    state: Literal["true", "false", "unknown"]
    authority: Literal["hub", "kernel", "device-control"]
    authority_ref: str | None = Field(default=None, max_length=255)
    observed_at: datetime


class BoundaryCapabilities(StrictModel):
    operation: Literal["admin.control-plane-capabilities"] = (
        "admin.control-plane-capabilities"
    )
    supported: tuple[str, ...]
    unavailable_without_producer_contract: tuple[str, ...]
    global_audit_projection_configured: Literal[False] = False
    admin_sqlite_authority: Literal[False] = False
