"""The internal ABI Local API calls, separate from the operator plane.

Its own prefix rather than another branch of ``/api/control-plane/v1``, because
that path family is already serving two audiences at once — a browser holding an
operator credential and a loopback service holding a service token — and adding
a third meaning to it is how the confusion this plan exists to remove got here.

Everything under here requires the Local API service credential. It is an
internal ABI: no browser reaches it, and the Owner it acts for arrives as an
argument from a boundary that authenticated a Controller, never as something a
caller may choose.

**No route here catches an ``AuthorityFailure``.** Letting it out is the
contract: ``control_plane.failure_handler`` converts it to a response and logs
it, once, for whatever is mounted on this app. Every route used to carry its own
copy of that conversion — twenty-eight of them — and the copies agreed on the
status while agreeing to record nothing, which is how a Host refused every
memory read for two weeks with no line in its journal saying why. A route that
re-adds an ``except`` here is re-adding that silence.
"""

from __future__ import annotations

from typing import Literal

from eidolon_sdk.biz.persona import PersonaAuthoring
from fastapi import APIRouter, Depends, Header, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from eidolon_admin_server.app.control_plane.contracts import MemoryMaterialization
from eidolon_admin_server.app.management.activity import (
    cancel_task,
    read_conversations,
    read_task,
    read_tasks,
    read_transcript,
    retry_task,
)
from eidolon_admin_server.app.management.activity_feed import read_activity
from eidolon_admin_server.app.management.context import read_context
from eidolon_admin_server.app.management.creation import create_companion
from eidolon_admin_server.app.management.faces import (
    clear_face,
    read_face,
    read_face_state,
    set_face,
)
from eidolon_admin_server.app.management.forgetting import apply_forget, propose_forget
from eidolon_admin_server.app.management.lifecycle import bring_back, put_away
from eidolon_admin_server.app.management.memory import (
    read_copy,
    read_day,
    read_graph,
    read_library,
)
from eidolon_admin_server.app.management.naming import rename_companion, rename_owner
from eidolon_admin_server.app.management.persona import (
    read_history,
    read_persona,
    restore_chapter,
    write_persona,
)
from eidolon_admin_server.app.management.recollecting import recall
from eidolon_admin_server.app.management.roster import (
    read_companion,
    read_roster,
    set_default_companion,
)
from eidolon_admin_server.app.management.sessions import revoke_runtime_sessions
from eidolon_admin_server.app.service_auth import require_local_api_credential
from eidolon_admin_server.conversation_identity import CONVERSATION_ID_MAX_LENGTH

#: Required by the router, so a second route here cannot be added without it.
router = APIRouter(
    prefix="/internal/v1/management",
    tags=["management-internal"],
    dependencies=[Depends(require_local_api_credential)],
)


class ManagementContextInternal(BaseModel):
    """What Local API projects into its public ``/context`` response."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"] = "1"
    operation: Literal["management.context"] = "management.context"
    owner_id: str = Field(min_length=1, max_length=64)
    owner_display_name: str = Field(default="", max_length=128)
    owner_revision: int = Field(ge=1)
    default_companion_id: str | None = Field(default=None, max_length=64)
    capabilities: dict[str, bool]
    #: Why each false capability is false. See the application projection for
    #: why the two reasons are not folded into one.
    unavailable: dict[str, str] = Field(default_factory=dict)
    limits: dict[str, int | None]


class CompanionSummaryInternal(BaseModel):
    """One roster row. No "is default" flag — see the page below."""

    model_config = ConfigDict(extra="forbid")

    companion_id: str = Field(min_length=1, max_length=64)
    display_name: str = Field(default="", max_length=128)
    kind: str = Field(min_length=1, max_length=32)
    lifecycle_state: str = Field(min_length=1, max_length=32)
    revision: int = Field(ge=1)
    created_at: str = Field(min_length=1, max_length=64)
    updated_at: str = Field(min_length=1, max_length=64)
    genome_id: str | None = Field(default=None, max_length=64)
    memory_realm_id: str | None = Field(default=None, max_length=64)
    #: Whether the runtime is holding this Companion right now. Null is
    #: **unknown** — the runtime could not be asked — and is a different answer
    #: from false. Several rows being true at once is ordinary (§4.6).
    running: bool | None = None
    #: When anything last addressed it, when the runtime said. Empty when
    #: unknown or when nothing has.
    last_active_at: str = Field(default="", max_length=64)


class CompanionRosterInternal(BaseModel):
    """A page of the Owner's roster, projected for Local API."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"] = "1"
    operation: Literal["companion.roster"] = "companion.roster"
    owner_id: str = Field(min_length=1, max_length=64)
    #: Named once for the page. A per-row flag would let two rows claim it.
    default_companion_id: str | None = Field(default=None, max_length=64)
    companions: list[CompanionSummaryInternal]
    #: Why the running column is unknown, when it is. Empty means the runtime
    #: answered — so every row's ``running`` is a real answer.
    runtime_unavailable: str = Field(default="", max_length=64)
    next_cursor: str | None = Field(default=None, max_length=256)


class CompanionDetailInternal(BaseModel):
    """One Companion, with the default comparison already made."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"] = "1"
    operation: Literal["companion.detail"] = "companion.detail"
    companion_id: str = Field(min_length=1, max_length=64)
    display_name: str = Field(default="", max_length=128)
    kind: str = Field(min_length=1, max_length=32)
    lifecycle_state: str = Field(min_length=1, max_length=32)
    revision: int = Field(ge=1)
    #: Derived from the Owner's pointer at read time, not stored anywhere. A
    #: single answer about a single Companion cannot contradict the roster,
    #: because both compute it from the same one field.
    is_default: bool


class DefaultCompanionRequestInternal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    companion_id: str = Field(min_length=1, max_length=64)
    #: Required here, unlike at the authority: this boundary has always just
    #: read the Owner, so a caller with no revision is a caller that skipped a
    #: read it was supposed to do.
    expected_revision: int = Field(ge=1)


class DefaultCompanionResponseInternal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"] = "1"
    operation: Literal["owner.default-companion"] = "owner.default-companion"
    default_companion_id: str | None = Field(default=None, max_length=64)


class CompanionCreateRequestInternal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=128)
    kind: str = Field(default="conversational", min_length=1, max_length=32)
    #: The SDK's shape, used rather than restated. Every hand-written copy of
    #: this is a place a field can be dropped in transit, and a dropped field
    #: here is a sentence somebody wrote about their Eidolon that never arrived.
    persona: PersonaAuthoring | None = None


class CompanionCreateResponseInternal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"] = "1"
    operation: Literal["companion.created"] = "companion.created"
    companion_id: str = Field(min_length=1, max_length=64)
    display_name: str = Field(default="", max_length=128)
    kind: str = Field(min_length=1, max_length=32)
    lifecycle_state: str = Field(min_length=1, max_length=32)
    revision: int = Field(ge=1)
    created: bool
    memory_ready: bool


class RenameRequestInternal(BaseModel):
    """One model for both renames: the rule is the same for a person and an
    Eidolon, and having two would eventually make them differ for no reason."""

    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=128)


class CompanionNameInternal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"] = "1"
    operation: Literal["companion.name"] = "companion.name"
    companion_id: str = Field(min_length=1, max_length=64)
    display_name: str = Field(default="", max_length=128)
    #: The version a later write compares against — it moved, because renaming
    #: writes the Companion.
    revision: int = Field(ge=1)


class OwnerNameInternal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"] = "1"
    operation: Literal["owner.name"] = "owner.name"
    owner_id: str = Field(min_length=1, max_length=64)
    display_name: str = Field(default="", max_length=128)
    revision: int = Field(ge=1)


class ActivityMomentInternal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1, max_length=64)
    action: str = Field(min_length=1, max_length=128)
    subject_type: str = Field(min_length=1, max_length=64)
    subject_id: str = Field(min_length=1, max_length=128)
    subject_name: str = Field(default="", max_length=128)
    occurred_at: str = Field(min_length=1, max_length=64)
    outcome: str = Field(min_length=1, max_length=16)
    detail: dict[str, str] = Field(default_factory=dict)


class ActivityFeedInternal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"] = "1"
    operation: Literal["owner.activity"] = "owner.activity"
    moments: list[ActivityMomentInternal]
    next_cursor: int | None = Field(default=None, ge=1)


class CompanionFaceInternal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"] = "1"
    operation: Literal["companion.face"] = "companion.face"
    companion_id: str = Field(min_length=1, max_length=64)
    has_face: bool
    sha256: str | None = Field(default=None, max_length=128)
    updated_at: str | None = Field(default=None, max_length=64)


class CompanionLifecycleRequestInternal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Only the two an Owner performs. ``retiring`` is a step on the way, not a
    #: place a person parks an Eidolon, and ``deleting`` belongs to a separate
    #: data-governance workflow that this surface must not be one field away
    #: from.
    lifecycle_state: Literal["archived", "active"]
    #: Who answers instead, when the one being put away is the one that answers.
    #: Named by the person, never chosen here.
    replacement_companion_id: str | None = Field(
        default=None, min_length=1, max_length=64
    )
    #: What the caller last saw. Optional: when it is absent the projection pins
    #: the write to the state it just read, so the change is a compare-and-set
    #: either way — the difference is only how wide the window is.
    expected_revision: int | None = Field(default=None, ge=1)


class CompanionLifecycleResponseInternal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"] = "1"
    operation: Literal["companion.lifecycle"] = "companion.lifecycle"
    companion_id: str = Field(min_length=1, max_length=64)
    lifecycle_state: str = Field(min_length=1, max_length=32)
    revision: int = Field(ge=1)
    #: Who answers for this Owner now, because archiving the one that did hands
    #: the role over in the same transaction.
    default_companion_id: str | None = Field(default=None, max_length=64)
    #: Devices that answered as this Eidolon and now answer as nobody.
    released_devices: list[str] = Field(default_factory=list)


class MemoryRoomInternal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    room_id: str = Field(min_length=1, max_length=256)
    entry_count: int = Field(ge=0)
    titles: list[str]
    more: bool


class MemoryWingInternal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    wing_id: str = Field(min_length=1, max_length=128)
    display_name: str = Field(default="", max_length=256)
    description: str = Field(default="", max_length=2048)
    entry_count: int = Field(ge=0)
    rooms: list[MemoryRoomInternal]


class MemoryLibraryInternal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"] = "1"
    operation: Literal["memory.library"] = "memory.library"
    memory_realm_id: str = Field(min_length=1, max_length=64)
    audience_scope: str = Field(min_length=1, max_length=128)
    materialization: MemoryMaterialization
    wings: list[MemoryWingInternal]
    entry_count: int = Field(ge=0)
    withheld_count: int = Field(ge=0)
    truncated: bool


class MemoryGraphNodeInternal(BaseModel):
    node_id: str
    label: str
    degree: int


class MemoryGraphEdgeInternal(BaseModel):
    edge_id: str
    subject: str
    predicate: str
    object: str
    confidence: float
    recorded_at: str = ""


class MemoryGraphInternal(BaseModel):
    contract_version: Literal["1"] = "1"
    operation: Literal["memory.graph"] = "memory.graph"
    nodes: list[MemoryGraphNodeInternal]
    edges: list[MemoryGraphEdgeInternal]
    truncated: bool


class MemoryEntryInternal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_id: str = Field(min_length=1, max_length=128)
    recorded_at: str = Field(min_length=1, max_length=64)
    recorded_at_source: str = Field(default="", max_length=64)
    wing_id: str = Field(default="", max_length=128)
    room_id: str = Field(default="", max_length=256)
    preview: str = Field(default="", max_length=4096)


class MemoryDayInternal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"] = "1"
    operation: Literal["memory.day"] = "memory.day"
    since: str = Field(min_length=1, max_length=64)
    entries: list[MemoryEntryInternal]
    entry_count: int = Field(ge=0)
    more_in_window: bool
    undated_count: int = Field(ge=0)
    truncated: bool


class RevokedSessionsInternal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"] = "1"
    operation: Literal["owner.runtime-sessions-revoked"] = (
        "owner.runtime-sessions-revoked"
    )
    #: The instant the runtime compares tokens against. Relayed, not re-stamped:
    #: a second clock's "now" would be a different answer to the only question
    #: that matters here.
    revoked_at: str = Field(min_length=1, max_length=64)


class ConversationInternal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str = Field(min_length=1, max_length=CONVERSATION_ID_MAX_LENGTH)
    started_at: str = Field(default="", max_length=64)
    updated_at: str = Field(default="", max_length=64)
    #: Absent while it has not ended — which is not the same as ended at an
    #: unknown time.
    ended_at: str | None = Field(default=None, max_length=64)


class ConversationPageInternal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"] = "1"
    operation: Literal["companion.conversations"] = "companion.conversations"
    companion_id: str = Field(min_length=1, max_length=64)
    conversations: list[ConversationInternal]
    next_cursor: str | None = Field(default=None, max_length=256)


class SpokenMessageInternal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str = Field(min_length=1, max_length=32)
    text: str = Field(default="", max_length=1_048_576)


class TranscriptTurnInternal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turn_id: str = Field(min_length=1, max_length=64)
    started_at: str = Field(default="", max_length=64)
    #: Absent while a turn is still going, which is what a dropped connection
    #: looks like in a transcript.
    finished_at: str | None = Field(default=None, max_length=64)
    status: str = Field(default="", max_length=32)
    messages: list[SpokenMessageInternal]


class TranscriptInternal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"] = "1"
    operation: Literal["companion.transcript"] = "companion.transcript"
    conversation_id: str = Field(min_length=1, max_length=CONVERSATION_ID_MAX_LENGTH)
    turns: list[TranscriptTurnInternal]
    next_cursor: str | None = Field(default=None, max_length=256)


class TaskInternal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1, max_length=64)
    #: The runtime's word, relayed rather than narrowed: the vocabulary is the
    #: Agent's, and a state this release has not heard of must not make a page
    #: unopenable.
    status: str = Field(min_length=1, max_length=32)
    asked: str = Field(default="", max_length=8192)
    kind: str = Field(default="", max_length=64)
    urgency: str = Field(default="", max_length=32)
    expected_output: str = Field(default="", max_length=4096)
    progress: str = Field(default="", max_length=8192)
    result: str = Field(default="", max_length=65536)
    error_code: str = Field(default="", max_length=128)
    error_message: str = Field(default="", max_length=4096)
    created_at: str = Field(default="", max_length=64)
    updated_at: str = Field(default="", max_length=64)
    completed_at: str | None = Field(default=None, max_length=64)


class TaskPageInternal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"] = "1"
    operation: Literal["companion.tasks"] = "companion.tasks"
    companion_id: str = Field(min_length=1, max_length=64)
    tasks: list[TaskInternal]
    next_cursor: str | None = Field(default=None, max_length=256)


class PersonaChapterInternal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter_id: str = Field(min_length=1, max_length=64)
    changed_at: str = Field(min_length=1, max_length=64)
    #: As written by whatever made the change. Empty stays empty: a sentence
    #: about who someone's Eidolon became is not this layer's to compose.
    what_changed: str = Field(default="", max_length=4096)
    #: Set when this chapter exists because someone went back to an earlier one.
    restored_from: int | None = None
    is_current: bool = False


class PersonaHistoryInternal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"] = "1"
    operation: Literal["companion.persona-history"] = "companion.persona-history"
    companion_id: str = Field(min_length=1, max_length=64)
    chapters: list[PersonaChapterInternal]


class PersonaRestoreInternal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter_id: str = Field(min_length=1, max_length=64)


class RecollectionInternal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(default="", max_length=8192)
    #: Absent stays absent. Filling it in with the time of asking would put a
    #: date on a memory that never had one.
    remembered_at: str | None = Field(default=None, max_length=64)


class RecollectionsInternal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"] = "1"
    operation: Literal["memory.recollections"] = "memory.recollections"
    #: Echoed: an empty answer with no question attached cannot be told apart
    #: from an answer to a different one.
    query: str = Field(min_length=1, max_length=256)
    recollections: list[RecollectionInternal]


class MemoryExportRecordInternal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_id: str = Field(min_length=1, max_length=128)
    #: Empty when nothing on the record gives a time. Those travel at the end of
    #: the file rather than being left out of it.
    recorded_at: str = Field(default="", max_length=64)
    recorded_at_source: str = Field(default="", max_length=64)
    wing_id: str = Field(default="", max_length=128)
    room_id: str = Field(default="", max_length=256)
    memory_type: str = Field(default="", max_length=64)
    #: Whole, and required: this is what the copy is of.
    value: str = Field(max_length=65536)


class MemoryCopyInternal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"] = "1"
    operation: Literal["memory.copy"] = "memory.copy"
    taken_at: str = Field(min_length=1, max_length=64)
    records: list[MemoryExportRecordInternal]
    record_count: int = Field(ge=0)
    undated_count: int = Field(ge=0)
    truncated: bool


class ForgetTargetInternal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str = Field(min_length=1, max_length=512)
    action: str = Field(default="delete", min_length=1, max_length=16)


class ForgetConfirmInternal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmation_token: str = Field(min_length=1, max_length=4096)


class ForgetEntryInternal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_id: str = Field(min_length=1, max_length=128)
    preview: str = Field(default="", max_length=4096)
    score: float = Field(ge=0.0, le=1.0)


class ForgetProposalInternal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"] = "1"
    operation: Literal["memory.forget-proposal"] = "memory.forget-proposal"
    status: Literal["preview", "not_found", "too_broad"]
    target: str = Field(min_length=1, max_length=512)
    action: str | None = Field(default=None, max_length=16)
    entries: list[ForgetEntryInternal]
    needs_confirmation: bool
    confirmation_token: str | None = Field(default=None, max_length=4096)
    expires_at: int | None = None
    detail: str = Field(default="", max_length=1024)


class ForgetResultInternal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"] = "1"
    operation: Literal["memory.forgotten"] = "memory.forgotten"
    action: str = Field(min_length=1, max_length=16)
    target: str = Field(min_length=1, max_length=512)
    entry_count: int = Field(ge=0)
    status: str = Field(min_length=1, max_length=64)


@router.get("/context", response_model=ManagementContextInternal)
async def get_context(
    request: Request,
    owner_id: str,
) -> ManagementContextInternal:
    """The Owner's context, for the Owner the caller already authenticated.

    ``owner_id`` is a query parameter and that is not a way for a caller to pick
    an Owner: the only caller is Local API, holding a service credential, and it
    passes the Owner bound to the Controller session it just verified. The
    authority checks ownership again on every read it serves.
    """
    context = await read_context(
        owner_id=owner_id,
        # The Owner aggregate is the workspace authority's, not the
        # Companion authority's. Reaching for ``.data`` here raised
        # AttributeError at runtime and no test noticed, because every test
        # injected its own reader. test_management_composition.py now
        # asserts the composed service satisfies these Protocols.
        owners=request.app.state.control_plane.workspace,
        # The composed service, because "which authorities does this Host hold a
        # key to" is a fact about the whole of it rather than about one client.
        credentials=request.app.state.control_plane,
    )
    return ManagementContextInternal(
        owner_id=context.owner_id,
        owner_display_name=context.owner_display_name,
        owner_revision=context.owner_revision,
        default_companion_id=context.default_companion_id,
        capabilities=context.capabilities,
        unavailable=context.unavailable,
        limits=context.limits,
    )


@router.get("/companions", response_model=CompanionRosterInternal)
async def list_companions(
    request: Request,
    owner_id: str,
    cursor: str | None = None,
) -> CompanionRosterInternal:
    """One page of this Owner's Companions.

    ``cursor`` is forwarded to the authority untouched and never interpreted
    here; the page boundary belongs to whoever built the page.
    """
    control_plane = request.app.state.control_plane
    roster = await read_roster(
        owner_id=owner_id,
        companions=control_plane.data,
        # The runtime is the Agent's to report, and its absence costs the
        # running column rather than the roster: somebody should still see what
        # Eidolons they have while the process that runs them is restarting.
        runtime=getattr(control_plane, "activity", None),
        cursor=cursor,
    )
    return CompanionRosterInternal(
        owner_id=roster.owner_id,
        default_companion_id=roster.default_companion_id,
        companions=[
            CompanionSummaryInternal(
                companion_id=row.companion_id,
                display_name=row.display_name,
                kind=row.kind,
                lifecycle_state=row.lifecycle_state,
                revision=row.revision,
                created_at=row.created_at,
                updated_at=row.updated_at,
                genome_id=row.genome_id,
                memory_realm_id=row.memory_realm_id,
                running=row.running,
                last_active_at=row.last_active_at,
            )
            for row in roster.companions
        ],
        next_cursor=roster.next_cursor,
        runtime_unavailable=roster.runtime_unavailable,
    )


@router.get("/companions/{companion_id}", response_model=CompanionDetailInternal)
async def get_companion(
    companion_id: str,
    request: Request,
    owner_id: str,
) -> CompanionDetailInternal:
    """One of this Owner's Companions.

    Ownership is proved by the authority, on a route that requires the Owner in
    its path. A Companion belonging to someone else is 404 rather than 403, so
    an identifier cannot be probed for existence.
    """
    detail = await read_companion(
        owner_id=owner_id,
        companion_id=companion_id,
        companions=request.app.state.control_plane.data,
        owners=request.app.state.control_plane.workspace,
    )
    return CompanionDetailInternal(
        companion_id=detail.companion_id,
        display_name=detail.display_name,
        kind=detail.kind,
        lifecycle_state=detail.lifecycle_state,
        revision=detail.revision,
        is_default=detail.is_default,
    )


@router.put(
    "/owners/default-companion", response_model=DefaultCompanionResponseInternal
)
async def put_default_companion(
    request: Request,
    owner_id: str,
    payload: DefaultCompanionRequestInternal,
) -> DefaultCompanionResponseInternal:
    """Move this Owner's default to one of their Companions."""
    default_companion_id = await set_default_companion(
        owner_id=owner_id,
        companion_id=payload.companion_id,
        expected_revision=payload.expected_revision,
        owners=request.app.state.control_plane.workspace,
    )
    return DefaultCompanionResponseInternal(default_companion_id=default_companion_id)


@router.get("/persona-authoring-template", response_model=PersonaAuthoring)
async def get_persona_authoring_template(request: Request) -> PersonaAuthoring:
    """The starting point a create form shows, from the authority that writes it.

    Owner-independent, so it takes no Owner — a product default is nobody's
    data, and asking per Owner would invite a per-Owner default nobody asked
    for.

    Relayed rather than answered here even though this process holds the same
    SDK. The value of the read is that it is what *Data* would write, and a copy
    on this side would be a second default personality that agrees right up
    until one of the two is upgraded.

    From the persona authority, beside the genome routes. It was first taken
    from the Owner-scoped one because that is where provisioning lives, which
    put the Owner/Companion boundary one route to the left of where it is.
    """

    return await request.app.state.control_plane.data.persona_authoring_template()


@router.put(
    "/companion-provisions/{operation_id}",
    response_model=CompanionCreateResponseInternal,
)
async def put_companion_provision(
    operation_id: str,
    request: Request,
    owner_id: str,
    payload: CompanionCreateRequestInternal,
) -> CompanionCreateResponseInternal:
    """Add a Companion to this Owner, exactly once per operation id."""
    control_plane = request.app.state.control_plane
    created = await create_companion(
        owner_id=owner_id,
        operation_id=operation_id,
        display_name=payload.display_name,
        kind=payload.kind,
        persona=payload.persona,
        companions=control_plane.workspace,
        memory=getattr(control_plane, "memory_supervisor", None),
    )
    return CompanionCreateResponseInternal(
        companion_id=created.companion_id,
        display_name=created.display_name,
        kind=created.kind,
        lifecycle_state=created.lifecycle_state,
        revision=created.revision,
        created=created.created,
        memory_ready=created.memory_ready,
    )


@router.get("/memory/library", response_model=MemoryLibraryInternal)
async def get_memory_library(
    request: Request,
    owner_id: str,
    companion_id: str | None = None,
) -> MemoryLibraryInternal:
    """What this Owner's memory holds, by wing and room.

    The physical Realm belongs to the Owner. ``companion_id`` selects a logical
    audience inside it: that Companion's private memories plus automatically
    derived Owner facts. Naming none returns only the derived Owner layer and
    never guesses a Companion.
    """
    library = await read_library(
        owner_id=owner_id,
        companion_id=companion_id,
        memory=request.app.state.control_plane.memory,
    )
    return MemoryLibraryInternal(
        memory_realm_id=library.memory_realm_id,
        audience_scope=library.audience_scope,
        materialization=library.materialization,
        wings=[
            MemoryWingInternal(
                wing_id=wing.wing_id,
                display_name=wing.display_name,
                description=wing.description,
                entry_count=wing.entry_count,
                rooms=[
                    MemoryRoomInternal(
                        room_id=room.room_id,
                        entry_count=room.entry_count,
                        titles=list(room.titles),
                        more=room.more,
                    )
                    for room in wing.rooms
                ],
            )
            for wing in library.wings
        ],
        entry_count=library.entry_count,
        withheld_count=library.withheld_count,
        truncated=library.truncated,
    )


@router.get("/memory/graph", response_model=MemoryGraphInternal)
async def get_memory_graph(
    request: Request,
    owner_id: str,
    companion_id: str | None = None,
) -> MemoryGraphInternal:
    graph = await read_graph(
        owner_id=owner_id,
        companion_id=companion_id,
        memory=request.app.state.control_plane.memory,
    )
    return MemoryGraphInternal(
        nodes=[MemoryGraphNodeInternal(**node.model_dump()) for node in graph.nodes],
        edges=[MemoryGraphEdgeInternal(**edge.model_dump()) for edge in graph.edges],
        truncated=graph.truncated,
    )


@router.post("/memory/forget/preview", response_model=ForgetProposalInternal)
async def post_forget_preview(
    request: Request,
    owner_id: str,
    payload: ForgetTargetInternal,
) -> ForgetProposalInternal:
    """What forgetting this would remove, without removing it."""
    proposal = await propose_forget(
        owner_id=owner_id,
        target=payload.target,
        action=payload.action,
        memory=request.app.state.control_plane.memory,
    )
    return ForgetProposalInternal(
        status=proposal.status,
        target=proposal.target,
        action=proposal.action,
        entries=[
            ForgetEntryInternal(
                entry_id=entry.entry_id, preview=entry.preview, score=entry.score
            )
            for entry in proposal.entries
        ],
        needs_confirmation=proposal.needs_confirmation,
        confirmation_token=proposal.confirmation_token,
        expires_at=proposal.expires_at,
        detail=proposal.detail,
    )


@router.post("/memory/forget/confirm", response_model=ForgetResultInternal)
async def post_forget_confirm(
    request: Request,
    owner_id: str,
    payload: ForgetConfirmInternal,
) -> ForgetResultInternal:
    """Apply exactly the set a preview bound."""
    result = await apply_forget(
        owner_id=owner_id,
        confirmation_token=payload.confirmation_token,
        memory=request.app.state.control_plane.memory,
    )
    return ForgetResultInternal(
        action=result.action,
        target=result.target,
        entry_count=result.entry_count,
        status=result.status,
    )


@router.post(
    "/owner/runtime-session-revocations",
    response_model=RevokedSessionsInternal,
)
async def post_runtime_session_revocation(
    request: Request,
    owner_id: str,
) -> RevokedSessionsInternal:
    """Sign every one of this Owner's devices out.

    Their Controller access is untouched: a runtime session is what a device uses
    to talk to a Companion, and a Controller grant is what a phone uses to manage
    the Host. Ending the first does not end the second, and the copy above this
    has to say so — "sign every device out" reads like it might lock someone out
    of their own management app.
    """
    revoked = await revoke_runtime_sessions(
        owner_id=owner_id,
        sessions=request.app.state.control_plane.activity,
    )
    return RevokedSessionsInternal(revoked_at=revoked.revoked_at)


@router.get(
    "/companions/{companion_id}/conversations",
    response_model=ConversationPageInternal,
)
async def get_companion_conversations(
    request: Request,
    companion_id: str,
    owner_id: str,
    limit: int | None = None,
    cursor: str | None = None,
) -> ConversationPageInternal:
    """When this Companion and its Owner talked.

    No message bodies: the runtime keeps those per turn and its turn rows carry
    none, so a list here would be timestamps with nothing said.
    """
    page = await read_conversations(
        owner_id=owner_id,
        companion_id=companion_id,
        limit=limit,
        cursor=cursor,
        activity=request.app.state.control_plane.activity,
    )
    return ConversationPageInternal(
        companion_id=companion_id,
        conversations=[
            ConversationInternal(
                conversation_id=row.conversation_id,
                started_at=row.started_at,
                updated_at=row.updated_at,
                ended_at=row.ended_at,
            )
            for row in page.conversations
        ],
        next_cursor=page.next_cursor,
    )


@router.get(
    "/companions/{companion_id}/conversations/{conversation_id}/turns",
    response_model=TranscriptInternal,
)
async def get_conversation_transcript(
    request: Request,
    companion_id: str,
    conversation_id: str,
    owner_id: str,
    limit: int | None = None,
    cursor: str | None = None,
) -> TranscriptInternal:
    """What was said in one conversation.

    Only what a person and their Eidolon said. Tool traffic is how an answer was
    reached rather than the conversation, and it can carry anything the tools
    touched — so it is dropped here and no client has to decide again.

    ``companion_id`` is in the path because that is where a conversation sits in
    this surface's shape; the check is on the Owner, which is what the runtime
    keys the conversation by.
    """
    transcript = await read_transcript(
        owner_id=owner_id,
        companion_id=companion_id,
        conversation_id=conversation_id,
        limit=limit,
        cursor=cursor,
        activity=request.app.state.control_plane.activity,
    )
    return TranscriptInternal(
        conversation_id=transcript.conversation_id,
        turns=[
            TranscriptTurnInternal(
                turn_id=turn.turn_id,
                started_at=turn.started_at,
                finished_at=turn.finished_at,
                status=turn.status,
                messages=[
                    SpokenMessageInternal(role=message.role, text=message.text)
                    for message in turn.messages
                ],
            )
            for turn in transcript.turns
        ],
        next_cursor=transcript.next_cursor,
    )


@router.get("/companions/{companion_id}/tasks", response_model=TaskPageInternal)
async def get_companion_tasks(
    request: Request,
    companion_id: str,
    owner_id: str,
    limit: int | None = None,
    status: str | None = None,
    cursor: str | None = None,
) -> TaskPageInternal:
    """What this Companion was asked to do, and how far it has got."""
    page = await read_tasks(
        owner_id=owner_id,
        companion_id=companion_id,
        limit=limit,
        status=status,
        cursor=cursor,
        activity=request.app.state.control_plane.activity,
    )
    return _task_page_internal(companion_id, page)


@router.get(
    "/companions/{companion_id}/tasks/{task_id}",
    response_model=TaskInternal,
)
async def get_companion_task(
    request: Request,
    companion_id: str,
    task_id: str,
    owner_id: str,
) -> TaskInternal:
    """One task. ``companion_id`` is in the path for the shape of the resource;
    the Owner is what the check is made against, because the runtime keys a task
    by its id alone."""
    task = await read_task(
        owner_id=owner_id,
        task_id=task_id,
        activity=request.app.state.control_plane.activity,
    )
    return _task_internal(task)


@router.post(
    "/companions/{companion_id}/tasks/{task_id}/cancel",
    response_model=TaskInternal,
)
async def post_task_cancel(
    request: Request,
    companion_id: str,
    task_id: str,
    owner_id: str,
) -> TaskInternal:
    """Stop it, and answer with what the runtime says it became.

    ``POST`` rather than ``PUT``: this is not a desired end state a retry can
    repeat blindly. Whether a task may be cancelled depends on what it is doing
    *now*, and that rule lives in the runtime — including its refusal when the
    task finished between the page being read and the button being pressed.
    """
    task = await cancel_task(
        owner_id=owner_id,
        task_id=task_id,
        activity=request.app.state.control_plane.activity,
    )
    return _task_internal(task)


@router.post(
    "/companions/{companion_id}/tasks/{task_id}/retry",
    response_model=TaskInternal,
)
async def post_task_retry(
    request: Request,
    companion_id: str,
    task_id: str,
    owner_id: str,
) -> TaskInternal:
    """Ask for it again. The runtime decides whether that is possible."""
    task = await retry_task(
        owner_id=owner_id,
        task_id=task_id,
        activity=request.app.state.control_plane.activity,
    )
    return _task_internal(task)


def _task_internal(task) -> TaskInternal:
    return TaskInternal(
        task_id=task.task_id,
        status=task.status,
        asked=task.asked,
        kind=task.kind,
        urgency=task.urgency,
        expected_output=task.expected_output,
        progress=task.progress,
        result=task.result,
        error_code=task.error_code,
        error_message=task.error_message,
        created_at=task.created_at,
        updated_at=task.updated_at,
        completed_at=task.completed_at,
    )


def _task_page_internal(companion_id: str, page) -> TaskPageInternal:
    return TaskPageInternal(
        companion_id=companion_id,
        tasks=[_task_internal(task) for task in page.tasks],
        next_cursor=page.next_cursor,
    )


@router.get("/companions/{companion_id}/persona", response_model=PersonaAuthoring)
async def get_persona(
    request: Request,
    companion_id: str,
    owner_id: str,
) -> PersonaAuthoring:
    """Who this Eidolon is now, in the part somebody wrote."""
    return await read_persona(
        owner_id=owner_id,
        companion_id=companion_id,
        persona=request.app.state.control_plane.data,
        companions=request.app.state.control_plane.data,
    )


@router.put("/companions/{companion_id}/persona", response_model=PersonaAuthoring)
async def put_persona(
    request: Request,
    companion_id: str,
    owner_id: str,
    payload: PersonaAuthoring,
) -> PersonaAuthoring:
    """Say who this Eidolon is now. Appends a chapter; never edits one."""
    return await write_persona(
        owner_id=owner_id,
        companion_id=companion_id,
        authored=payload,
        persona=request.app.state.control_plane.data,
        companions=request.app.state.control_plane.data,
    )


@router.get(
    "/companions/{companion_id}/persona-history",
    response_model=PersonaHistoryInternal,
)
async def get_persona_history(
    request: Request,
    companion_id: str,
    owner_id: str,
) -> PersonaHistoryInternal:
    """What this Eidolon has been.

    ``owner_id`` is what makes this safe: the persona routes are keyed on a
    Companion alone, so ownership is proved through the owner-scoped Companion
    route before either of them is called. Someone else's Companion is a 404.
    """
    history = await read_history(
        owner_id=owner_id,
        companion_id=companion_id,
        persona=request.app.state.control_plane.data,
        companions=request.app.state.control_plane.data,
    )
    return _persona_history_internal(history)


@router.put(
    "/companions/{companion_id}/persona-restorations",
    response_model=PersonaHistoryInternal,
)
async def put_persona_restoration(
    request: Request,
    companion_id: str,
    owner_id: str,
    payload: PersonaRestoreInternal,
) -> PersonaHistoryInternal:
    """Make this Eidolon the way it was then.

    ``PUT``: the body names the chapter this Companion should be, so the same
    request twice leaves it as the same thing — the projection reads the current
    chapter first and a repeat is answered with the history unchanged rather than
    with the authority's "nothing to append" conflict.

    Answers with the history rather than the new chapter, because what someone
    wants after going back is where that leaves them.
    """
    history = await restore_chapter(
        owner_id=owner_id,
        companion_id=companion_id,
        chapter_id=payload.chapter_id,
        persona=request.app.state.control_plane.data,
        companions=request.app.state.control_plane.data,
    )
    return _persona_history_internal(history)


@router.get("/activity", response_model=ActivityFeedInternal)
async def get_activity(
    request: Request,
    owner_id: str,
    limit: int | None = Query(default=None, ge=1, le=100),
    before: int | None = Query(default=None, ge=1),
) -> ActivityFeedInternal:
    """What has been done to this Owner's things lately."""
    feed = await read_activity(
        owner_id=owner_id,
        limit=limit,
        before=before,
        history=request.app.state.control_plane.workspace,
        companions=request.app.state.control_plane.data,
    )
    return ActivityFeedInternal(
        moments=[
            ActivityMomentInternal(
                event_id=moment.event_id,
                action=moment.action,
                subject_type=moment.subject_type,
                subject_id=moment.subject_id,
                subject_name=moment.subject_name,
                occurred_at=moment.occurred_at,
                outcome=moment.outcome,
                detail=moment.detail,
            )
            for moment in feed.moments
        ],
        next_cursor=feed.next_cursor,
    )


@router.get(
    "/companions/{companion_id}/face-state", response_model=CompanionFaceInternal
)
async def get_companion_face_state(
    request: Request,
    companion_id: str,
    owner_id: str,
) -> CompanionFaceInternal:
    """Whether there is a face, without carrying one."""
    view = await read_face_state(
        owner_id=owner_id,
        companion_id=companion_id,
        companions=request.app.state.control_plane.data,
        faces=request.app.state.control_plane.data,
    )
    return _face_internal(view)


@router.get(
    "/companions/{companion_id}/face",
    response_class=Response,
    responses={
        200: {"content": {"image/jpeg": {}}},
        204: {"description": "This Eidolon has no face"},
        304: {"description": "The face the caller already holds"},
    },
)
async def get_companion_face(
    request: Request,
    companion_id: str,
    owner_id: str,
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
) -> Response:
    """The face itself.

    Bytes stay bytes the whole way. Every layer between the authority and the
    phone would otherwise encode and decode a photograph to say the same thing,
    and none of them has any use for what is inside it.
    """
    face = await read_face(
        owner_id=owner_id,
        companion_id=companion_id,
        known_sha256=_etag_digest(if_none_match),
        companions=request.app.state.control_plane.data,
        faces=request.app.state.control_plane.data,
    )
    if face.unchanged:
        return Response(status_code=304, headers=_face_etag(face.sha256))
    if face.content is None:
        return Response(status_code=204)
    return Response(
        content=face.content,
        media_type="image/jpeg",
        headers=_face_etag(face.sha256),
    )


@router.put("/companions/{companion_id}/face", response_model=CompanionFaceInternal)
async def put_companion_face(
    request: Request,
    companion_id: str,
    owner_id: str,
) -> CompanionFaceInternal:
    """Give this Eidolon a face. The photograph is the body of the request."""
    view = await set_face(
        owner_id=owner_id,
        companion_id=companion_id,
        face=await request.body(),
        companions=request.app.state.control_plane.data,
        faces=request.app.state.control_plane.data,
    )
    return _face_internal(view)


@router.delete("/companions/{companion_id}/face", response_model=CompanionFaceInternal)
async def delete_companion_face(
    request: Request,
    companion_id: str,
    owner_id: str,
) -> CompanionFaceInternal:
    view = await clear_face(
        owner_id=owner_id,
        companion_id=companion_id,
        companions=request.app.state.control_plane.data,
        faces=request.app.state.control_plane.data,
    )
    return _face_internal(view)


def _face_internal(view) -> CompanionFaceInternal:
    return CompanionFaceInternal(
        companion_id=view.companion_id,
        has_face=view.has_face,
        sha256=view.sha256,
        updated_at=view.updated_at,
    )


def _face_etag(digest: str | None) -> dict[str, str]:
    return {"ETag": f'"sha256:{digest}"'} if digest else {}


def _etag_digest(header: str | None) -> str | None:
    """The digest inside an ``If-None-Match``, when it is one this Host wrote.

    Anything else — a wildcard, a weak validator, an ETag from some other
    service — is read as "I have nothing", which costs a photograph and is the
    safe direction to be wrong in.
    """

    if not header:
        return None
    value = header.strip().strip('"')
    prefix = "sha256:"
    return value[len(prefix) :] if value.startswith(prefix) else None


@router.patch("/companions/{companion_id}", response_model=CompanionNameInternal)
async def patch_companion(
    request: Request,
    companion_id: str,
    owner_id: str,
    payload: RenameRequestInternal,
) -> CompanionNameInternal:
    """Set what this Owner calls their Eidolon."""
    identity = await rename_companion(
        owner_id=owner_id,
        companion_id=companion_id,
        display_name=payload.display_name,
        companions=request.app.state.control_plane.data,
        namer=request.app.state.control_plane.data,
    )
    return CompanionNameInternal(
        companion_id=identity.companion_id,
        display_name=identity.display_name,
        revision=identity.revision,
    )


@router.patch("/owner", response_model=OwnerNameInternal)
async def patch_owner(
    request: Request,
    owner_id: str,
    payload: RenameRequestInternal,
) -> OwnerNameInternal:
    """Set what this person is called.

    No Owner in the path, and none to prove against: the Owner *is* the scope
    here, and it arrived from the boundary that authenticated a Controller.
    """
    owner = await rename_owner(
        owner_id=owner_id,
        display_name=payload.display_name,
        namer=request.app.state.control_plane.workspace,
    )
    return OwnerNameInternal(
        owner_id=owner.owner_id,
        display_name=owner.display_name,
        revision=owner.revision,
    )


@router.put(
    "/companions/{companion_id}/lifecycle",
    response_model=CompanionLifecycleResponseInternal,
)
async def put_companion_lifecycle(
    request: Request,
    companion_id: str,
    owner_id: str,
    payload: CompanionLifecycleRequestInternal,
) -> CompanionLifecycleResponseInternal:
    """Put this Eidolon away, or bring it back.

    One route for both, because the body states where it should end up. That is
    what makes the retry a phone sends after a lost answer safe, and it is why
    asking for the state it is already in succeeds instead of conflicting.

    The refusals matter as much as the success here, so they are relayed with
    the authority's own code: "name someone to answer instead" is a question the
    person can answer, and it must not arrive looking like a lost race.
    """
    if payload.lifecycle_state == "archived":
        view = await put_away(
            owner_id=owner_id,
            companion_id=companion_id,
            replacement_companion_id=payload.replacement_companion_id,
            expected_revision=payload.expected_revision,
            lifecycle=request.app.state.control_plane.workspace,
            # Kernel's, through the service that already holds that client: which
            # device answers as which Eidolon is a fact about the body mesh.
            bodies=request.app.state.control_plane,
        )
    else:
        view = await bring_back(
            owner_id=owner_id,
            companion_id=companion_id,
            expected_revision=payload.expected_revision,
            lifecycle=request.app.state.control_plane.workspace,
        )
    return CompanionLifecycleResponseInternal(
        companion_id=view.companion_id,
        lifecycle_state=view.lifecycle_state,
        revision=view.revision,
        default_companion_id=view.default_companion_id,
        released_devices=list(view.released_devices),
    )


def _persona_history_internal(history) -> PersonaHistoryInternal:
    return PersonaHistoryInternal(
        companion_id=history.companion_id,
        chapters=[
            PersonaChapterInternal(
                chapter_id=chapter.chapter_id,
                changed_at=chapter.changed_at,
                what_changed=chapter.what_changed,
                restored_from=chapter.restored_from,
                is_current=chapter.is_current,
            )
            for chapter in history.chapters
        ],
    )


@router.get("/memory/recollections", response_model=RecollectionsInternal)
async def get_memory_recollections(
    request: Request,
    owner_id: str,
    # The same bounds the public route declares. Repeated rather than trusted:
    # this ABI has one caller today, and "the caller validates it" is the
    # assumption that stops being true when a second one arrives.
    q: str = Query(min_length=1, max_length=256),
    limit: int = Query(default=10, ge=1, le=50),
    companion_id: str | None = None,
) -> RecollectionsInternal:
    """What this Eidolon remembers about something.

    A sentence and a time. The wings, rooms and scores memory carries are how it
    found something rather than what it remembers, and dropping them here means
    no client has to decide that again.
    """
    found = await recall(
        owner_id=owner_id,
        query=q,
        limit=limit,
        companion_id=companion_id,
        memory=request.app.state.control_plane.memory,
    )
    return RecollectionsInternal(
        query=found.query,
        recollections=[
            RecollectionInternal(text=entry.text, remembered_at=entry.remembered_at)
            for entry in found.recollections
        ],
    )


@router.get("/memory/export", response_model=MemoryCopyInternal)
async def get_memory_export(
    request: Request,
    owner_id: str,
    companion_id: str | None = None,
) -> MemoryCopyInternal:
    """A copy of this memory the person can read and keep.

    A read, so a GET. Not the Host backup — that copy is the palace itself, it
    is taken by the operator tool through memory's own admin action, and it never
    passes through here.
    """
    copy = await read_copy(
        owner_id=owner_id,
        companion_id=companion_id,
        memory=request.app.state.control_plane.memory,
    )
    return MemoryCopyInternal(
        taken_at=copy.taken_at,
        records=[
            MemoryExportRecordInternal(
                entry_id=record.entry_id,
                recorded_at=record.recorded_at,
                recorded_at_source=record.recorded_at_source,
                wing_id=record.wing_id,
                room_id=record.room_id,
                memory_type=record.memory_type,
                value=record.value,
            )
            for record in copy.records
        ],
        record_count=copy.record_count,
        undated_count=copy.undated_count,
        truncated=copy.truncated,
    )


@router.get("/memory/entries", response_model=MemoryDayInternal)
async def get_memory_entries(
    request: Request,
    owner_id: str,
    since: str,
    limit: int | None = None,
    companion_id: str | None = None,
) -> MemoryDayInternal:
    """What was recorded at or after ``since``.

    ``since`` is required with no default, all the way down. A day depends on
    where the person is and no layer here knows; a default would answer for the
    wrong day without saying so.
    """
    day = await read_day(
        owner_id=owner_id,
        since=since,
        limit=limit,
        companion_id=companion_id,
        memory=request.app.state.control_plane.memory,
    )
    return MemoryDayInternal(
        since=day.since,
        entries=[
            MemoryEntryInternal(
                entry_id=entry.entry_id,
                recorded_at=entry.recorded_at,
                recorded_at_source=entry.recorded_at_source,
                wing_id=entry.wing_id,
                room_id=entry.room_id,
                preview=entry.preview,
            )
            for entry in day.entries
        ],
        entry_count=day.entry_count,
        more_in_window=day.more_in_window,
        undated_count=day.undated_count,
        truncated=day.truncated,
    )
