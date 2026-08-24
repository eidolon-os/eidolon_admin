"""Strict consumed contracts and Admin-owned control-plane read models."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from eidolon_sdk.biz.contracts.companion import CompanionLifecycleState
from eidolon_sdk.device_foundation.v1 import (
    BusinessOwnerId,
    ClaimQuery,
    ControllerActorRef,
    DecideEnrollment,
    DecideEnrollmentResult,
    DeviceRef,
    EnrollmentProposalQuery,
    EnrollmentRecoveryProjection,
    RevokeClaimResult as HubClaimRevocationResult,
)


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


class MemoryBrowse(StrictModel):
    """What an Owner's memory holds, by wing and room."""

    contract_version: Literal["1"]
    operation: Literal["memory.browse"]
    memory_space_id: str = Field(min_length=1, max_length=64)
    wings: tuple[MemoryWing, ...] = ()
    entry_count: int = Field(ge=0)
    #: Present and not listed — the Owner's own privacy wing is the common case.
    #: Carried through rather than dropped: a count that disagrees with what is
    #: listed is worse than a count that explains itself.
    withheld_count: int = Field(ge=0)
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


class MemoryAudience(StrictModel):
    """Which of this Owner's Companions a memory now belongs to.

    ``extra="allow"`` for the same reason :class:`ForgetOutcome` has it: the realm
    merges the command ledger's own status dictionary into the answer, and that
    vocabulary belongs to the ledger.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    contract_version: Literal["1"]
    operation: Literal["memory.audience"]
    entry_id: str = Field(min_length=1, max_length=128)
    #: The audience token the realm applied — ``owner`` or ``companion:<id>``.
    audience: str = Field(min_length=1, max_length=160)
    #: The Companion it names, echoed so nothing above has to parse the token.
    #: Empty means the Owner layer: every Companion may recall it again.
    companion_id: str = Field(default="", max_length=128)
    #: The ledger's word. Publishing is durable, applying is a projection still
    #: running, and which of the two happened is not this layer's to decide.
    status: str = Field(min_length=1, max_length=64)


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


class OwnerRecollections(StrictModel):
    """What an Owner's Eidolon holds about a question.

    The records themselves are passed through as memory shaped them rather
    than re-modelled here: Admin is projecting a boundary, not deciding what a
    memory is.
    """

    operation: Literal["owner.recollections"] = "owner.recollections"
    owner_id: str = Field(min_length=1, max_length=64)
    query: str
    recollections: list[dict[str, Any]]


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
    operation: Literal["kernel.device-mount"]
    device_id: str = Field(min_length=1, max_length=128)
    owner_id: str = Field(min_length=1, max_length=64)
    device_ref: DeviceRef
    attached_companion_id: str | None = Field(default=None, min_length=1, max_length=64)
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


class KernelMutationResult(StrictModel):
    operation: Literal["kernel.device-mount-mutation-result"]
    mount: KernelMount
    audit_position: int = Field(ge=1)
    replayed: bool


class ControllerEnrollmentQuery(StrictModel):
    """Authenticated Local context wrapped around the canonical Hub query."""

    contract_version: Literal["1"]
    actor: ControllerActorRef
    business_owner_id: BusinessOwnerId
    query: EnrollmentProposalQuery

    @model_validator(mode="after")
    def _scope(self) -> "ControllerEnrollmentQuery":
        if self.actor.owner_domain_id != self.query.owner_domain_id:
            raise ValueError("Enrollment query actor and Owner Domain do not match")
        if "device.read" not in self.actor.granted_scopes:
            raise ValueError("Enrollment query actor lacks device.read")
        return self


class ControllerClaimQuery(StrictModel):
    contract_version: Literal["1"]
    actor: ControllerActorRef
    business_owner_id: BusinessOwnerId
    query: ClaimQuery

    @model_validator(mode="after")
    def _scope(self) -> "ControllerClaimQuery":
        if self.actor.owner_domain_id != self.query.owner_domain_id:
            raise ValueError("Claim query actor and Owner Domain do not match")
        if "device.read" not in self.actor.granted_scopes:
            raise ValueError("Claim query actor lacks device.read")
        return self


class ControllerEnrollmentDecisionIntent(StrictModel):
    """Admin workflow input; the Decision itself is the SDK binding."""

    contract_version: Literal["1"]
    request_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    actor: ControllerActorRef
    decision: DecideEnrollment

    @model_validator(mode="after")
    def _scope(self) -> "ControllerEnrollmentDecisionIntent":
        if self.actor.owner_domain_id != self.decision.target_owner_domain_id:
            raise ValueError("Decision actor and target Owner Domain do not match")
        if "device.claim.approve" not in self.actor.granted_scopes:
            raise ValueError("Decision actor lacks device.claim.approve")
        return self


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


class WorkflowFailure(StrictModel):
    #: Kept in step with ``errors.FailureKind`` and ``errors.AuthorityFailure``:
    #: a value this model rejects raises inside the router's exception handler,
    #: which turns a chosen status into an unexplained 500.
    authority: Literal["directory", "data", "hub", "kernel", "memory"]
    kind: Literal[
        "unauthorized",
        "forbidden",
        "not_found",
        "conflict",
        "invalid_request",
        "unavailable",
        "runtime_missing",
        "upstream_failure",
        "contract_violation",
        "configuration",
    ]
    detail: str
    upstream_status: int | None = None
    retryable: bool


class WorkflowStep(StrictModel):
    name: Literal["hub_revocation"]
    state: Literal["committed", "replayed", "failed", "not_requested", "not_attempted"]
    request_id: str | None = None
    revision: int | None = None
    failure: WorkflowFailure | None = None


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
