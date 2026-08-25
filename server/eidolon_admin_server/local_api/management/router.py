"""The public Owner management surface: ``/api/management/v1``.

Its own module, not another block in ``app.py``. That file already mounts most
of the product and the plan's rule for this router is narrow enough to be worth
enforcing by shape: **authenticate, decode, call the backend, map the answer.**
No business judgement lives here, because this is the process that listens on
the LAN and deliberately holds no authority credential (plan §3.4.1) — a
decision made here would be a decision made on the wrong side of that boundary.

The Owner is never an input. It comes from the Controller session this router
authenticates, and is passed down as an argument.
"""

from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

MANAGEMENT_PREFIX = "/api/management/v1"


def _controller_view(row: dict, *, asking: str) -> "ControllerView":
    """One grant, as a person reads it.

    Strict about what it needs and forgiving about what it does not: a Host that
    grows a field is not a Host this list should refuse to draw.
    """

    controller_id = str(row.get("controller_id") or "")
    if not controller_id:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "Host answered with a controller that has no identifier",
        )
    return ControllerView(
        controller_id=controller_id,
        display_name=str(row.get("display_name") or ""),
        platform=str(row.get("platform") or ""),
        role=str(row.get("role") or "unknown"),
        fingerprint=str(row.get("public_key_fingerprint") or ""),
        claimed_at=str(row.get("created_at") or ""),
        is_you=controller_id == asking,
    )


def _refused(exc: "ManagementBackendError") -> HTTPException:
    """The one shape a refusal leaves this surface in.

    A plain sentence while the backend gave no code, and ``{"code", "message"}``
    when it did. Two shapes rather than always the object, because every client
    reading this API today expects the sentence, and a refusal is the worst
    moment to hand one something it cannot parse.
    """

    if exc.code is None:
        return HTTPException(exc.status_code, str(exc))
    return HTTPException(exc.status_code, {"code": exc.code, "message": str(exc)})


class ManagementBackendError(RuntimeError):
    """The backend refused or could not answer; carries the status to relay.

    ``code`` is the authority's own word for which refusal this is, when it gave
    one. It travels because some refusals are questions a person can answer —
    "this is the Eidolon that answers for you; who should answer instead?" — and
    a client that could only read a status would have to show the same shrug for
    that as for a lost race.
    """

    def __init__(
        self, message: str, *, status_code: int = 503, code: str | None = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class ManagementContextView(BaseModel):
    """What a client reads before it draws anything.

    ``capabilities`` is discovery, not permission: true means this Host can do
    the thing at all, and whether this Controller may is answered per action. A
    name absent from the map is one this client has never heard of — a version
    skew — while a name present and false is a feature this Host cannot do yet.

    ``limits`` values may be null, and a client must not substitute a number of
    its own: a limit nobody has measured is not a limit.
    """

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"] = "1"
    #: Deliberately no ``owner_id`` here — see the field note below.
    owner: "OwnerContextView"
    #: The Owner's pointer, named once. Null is a real state and no client may
    #: resolve it by choosing a Companion.
    default_companion_id: str | None = Field(default=None, max_length=64)
    capabilities: dict[str, bool]
    limits: dict[str, int | None]


class OwnerContextView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Returned because the client may need it for display and correlation, not
    #: because it may send one: a request that carried an ``owner_id`` would be
    #: asking this boundary to act for someone other than whoever it just
    #: authenticated, and no route here accepts one.
    owner_id: str = Field(min_length=1, max_length=64)
    display_name: str = Field(default="", max_length=128)
    #: The version a writer compares against when changing the default.
    revision: int = Field(ge=1)


class CompanionSummaryView(BaseModel):
    """One Eidolon, as a list shows it.

    No "is the default" flag. The page says which one is default exactly once,
    so a client cannot render two rows both claiming it, and cannot disagree
    with the Owner record about which one it is.
    """

    model_config = ConfigDict(extra="forbid")

    companion_id: str = Field(min_length=1, max_length=64)
    #: What the Owner named it. May be empty on a Host whose Data predates the
    #: name; a client shows its own placeholder rather than the identifier.
    display_name: str = Field(default="", max_length=128)
    #: Product type. A client must treat a value it does not know as "some
    #: other kind" and still render the row.
    kind: str = Field(min_length=1, max_length=32)
    #: Where it is in its life: active, retiring, archived, deleting. Four
    #: states rather than a boolean, because "the Owner archived it" and "it
    #: cannot run right now" are different things to show.
    lifecycle_state: str = Field(min_length=1, max_length=32)
    #: The version a later write compares against.
    revision: int = Field(ge=1)
    created_at: str = Field(min_length=1, max_length=64)
    updated_at: str = Field(min_length=1, max_length=64)


class CompanionRosterView(BaseModel):
    """One page of this Owner's Eidolons, oldest first."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"] = "1"
    #: Named once, for the page. Null is a real answer — every Companion
    #: archived, or the only one is a guard — and a client must show that state
    #: rather than promoting a row to fill the gap.
    default_companion_id: str | None = Field(default=None, max_length=64)
    companions: list[CompanionSummaryView]
    #: Opaque. A client stores it and sends it back to get the next page;
    #: parsing it would make the Host's page boundary part of the client.
    next_cursor: str | None = Field(default=None, max_length=256)


class CompanionDetailView(BaseModel):
    """One Eidolon, opened.

    ``is_default`` is here and *not* on a roster row on purpose. A single answer
    about a single Companion is a comparison the Host just made against the
    Owner's one pointer; a flag repeated across a list would let two rows claim
    it. Same fact, and only one shape of it can be self-contradictory.
    """

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"] = "1"
    companion_id: str = Field(min_length=1, max_length=64)
    display_name: str = Field(default="", max_length=128)
    kind: str = Field(min_length=1, max_length=32)
    lifecycle_state: str = Field(min_length=1, max_length=32)
    #: The version a later write must present. Read now so a client that is
    #: about to rename or archive is not made to fetch again first.
    revision: int = Field(ge=1)
    is_default: bool


class DefaultCompanionRequest(BaseModel):
    """Which of my Eidolons answers when I did not say which.

    ``expected_revision`` is not ceremony a client can skip: it is the version
    of the Owner it last read, and it is what makes two phones (or a phone and a
    browser) unable to both win. A client that has not read the Owner has no
    business changing this.
    """

    model_config = ConfigDict(extra="forbid")

    companion_id: str = Field(min_length=1, max_length=64)
    expected_revision: int = Field(ge=1)


class DefaultCompanionView(BaseModel):
    """Where the pointer now points, read back from the authority."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"] = "1"
    #: Echoed rather than assumed. A client that painted its own choice would be
    #: showing what it asked for instead of what happened.
    default_companion_id: str | None = Field(default=None, max_length=64)


class RenameRequest(BaseModel):
    """What to call it, as the person typed it.

    A name of only spaces passes a length check and would erase the one they
    have, so it is refused here — at the boundary they are actually typing into,
    rather than two services away where the answer would be the same but later.

    One model for a person and for an Eidolon on purpose. The rule is the same,
    and two models would eventually make it differ for no reason anybody could
    name.
    """

    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=128)

    @field_validator("display_name")
    @classmethod
    def _must_be_a_name(cls, value: str) -> str:
        name = value.strip()
        if not name:
            raise ValueError("display_name cannot be blank")
        return name


class CompanionNameView(BaseModel):
    """What this Eidolon is called, after being told."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"] = "1"
    companion_id: str = Field(min_length=1, max_length=64)
    display_name: str = Field(default="", max_length=128)
    #: Moved, because renaming writes the Companion. A client holding the old
    #: one would fail its next compare-and-set for a reason it could not see.
    revision: int = Field(ge=1)


class OwnerNameView(BaseModel):
    """What this person is called, after being told."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"] = "1"
    owner_id: str = Field(min_length=1, max_length=64)
    display_name: str = Field(default="", max_length=128)
    revision: int = Field(ge=1)


class CompanionFaceView(BaseModel):
    """Whether this Eidolon has a face — never the face itself.

    A screen asks this before deciding whether a photograph is worth fetching,
    and the answer is small enough to ask on every refresh.
    """

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"] = "1"
    companion_id: str = Field(min_length=1, max_length=64)
    has_face: bool
    #: Changes whenever the face does, which is what lets a client know its copy
    #: is stale without comparing photographs.
    sha256: str | None = Field(default=None, max_length=128)
    updated_at: str | None = Field(default=None, max_length=64)


class CompanionLifecycleRequest(BaseModel):
    """Where this Eidolon should be: put away, or here again.

    A desired end rather than a verb, so a phone that never saw the answer can
    send the same request again — and so a second tap on a button whose screen
    had not caught up is a success rather than an error.

    Only two states are expressible. ``retiring`` is a step the Host walks
    through on the way to archived, not somewhere a person parks an Eidolon, and
    deletion is a different conversation entirely: a surface where "put this
    away" and "erase this forever" are one field apart is a surface that will
    eventually erase something.
    """

    model_config = ConfigDict(extra="forbid")

    lifecycle_state: Literal["archived", "active"]
    #: Required only when putting away the Eidolon that answers when nobody was
    #: named — and the Host says so by refusing with
    #: ``default_replacement_required`` rather than the client guessing. Nothing
    #: on the Host will choose for the Owner.
    replacement_companion_id: str | None = Field(
        default=None, min_length=1, max_length=64
    )
    #: The revision this client last read, when it has one. Optional here, unlike
    #: the default-companion change: that one is a race between two people
    #: choosing differently, while this one is a state the request names
    #: outright, and a phone reopening a screen it had cached should not be
    #: forced to re-read a Companion to put it away.
    expected_revision: int | None = Field(default=None, ge=1)


class CompanionLifecycleView(BaseModel):
    """Where the Eidolon is now, and who answers for me.

    Both in one answer: putting away the one that answers hands the role over in
    the same breath, and a client that had to ask a second question would show
    "archived" beside a stale answer to "so who talks to me now".
    """

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"] = "1"
    companion_id: str = Field(min_length=1, max_length=64)
    #: Echoed from the Host. A client that painted its own request would be
    #: showing what it asked for rather than what happened.
    lifecycle_state: str = Field(min_length=1, max_length=32)
    revision: int = Field(ge=1)
    default_companion_id: str | None = Field(default=None, max_length=64)


class CompanionCreateRequest(BaseModel):
    """Ask for another Eidolon.

    ``operation_id`` is the client's, and it is what makes asking twice safe:
    every identifier the Host derives comes from it, so a retry addresses the
    same Eidolon rather than creating a second one. A client that generates a
    fresh id per attempt has opted out of that protection, which is why it is
    required rather than defaulted here.
    """

    model_config = ConfigDict(extra="forbid")

    operation_id: str = Field(min_length=36, max_length=36)
    display_name: str = Field(min_length=1, max_length=128)
    #: Absent means an ordinary conversational Eidolon. A client should not have
    #: to know the other values exist to create the normal thing.
    kind: str = Field(default="conversational", min_length=1, max_length=32)


class CompanionCreatedView(BaseModel):
    """The Eidolon that now exists, and whether its memory is up yet."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"] = "1"
    companion_id: str = Field(min_length=1, max_length=64)
    display_name: str = Field(default="", max_length=128)
    kind: str = Field(min_length=1, max_length=32)
    lifecycle_state: str = Field(min_length=1, max_length=32)
    revision: int = Field(ge=1)
    #: False when this request found the Eidolon already created by the same
    #: operation. It exists either way; a client that shows "created!" twice for
    #: one intent is telling the person something that did not happen.
    created: bool
    #: False means "its memory is not running yet", not "something is wrong".
    #: The Host converges on its own; a client should say "still coming up"
    #: rather than either hiding it or calling it a failure.
    memory_ready: bool


class MemoryRoomView(BaseModel):
    """One shelf of a person's memory."""

    model_config = ConfigDict(extra="forbid")

    room_id: str = Field(min_length=1, max_length=256)
    entry_count: int = Field(ge=0)
    #: A few titles, enough to recognise the shelf. Deliberately not its
    #: contents: a browse that returned everything would be an export wearing
    #: another name, and export is its own capability with its own consent.
    titles: list[str]
    #: True when the shelf holds more than the titles shown.
    more: bool


class MemoryWingView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    wing_id: str = Field(min_length=1, max_length=128)
    #: Empty when this Host has never heard of the category. A client must show
    #: its own words for that rather than the identifier — nobody named a memory
    #: "Wing_FromALaterRelease".
    display_name: str = Field(default="", max_length=256)
    description: str = Field(default="", max_length=2048)
    entry_count: int = Field(ge=0)
    rooms: list[MemoryRoomView]


class MemoryLibraryView(BaseModel):
    """What my Eidolon remembers, arranged the way it files it."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"] = "1"
    wings: list[MemoryWingView]
    entry_count: int = Field(ge=0)
    #: Here and not listed — things marked "do not bring this up", and (once
    #: anything is marked private to one Companion) another Companion's. A
    #: number rather than a silence: a total that disagreed with what is shown
    #: would look like a bug in the person's own memory.
    withheld_count: int = Field(ge=0)
    #: The Host read as much as it is willing to in one go. A client must not
    #: present a truncated library as the whole of someone's memory.
    truncated: bool


class MemoryEntryView(BaseModel):
    """One thing it wrote down."""

    model_config = ConfigDict(extra="forbid")

    entry_id: str = Field(min_length=1, max_length=128)
    #: When the memory is *about*, not when it was stored — the two differ when
    #: someone mentions last week.
    recorded_at: str = Field(min_length=1, max_length=64)
    #: Which field that time came from. A person never reads it; "它把这件事
    #: 记到昨天了" is a real complaint and this is what makes it answerable.
    recorded_at_source: str = Field(default="", max_length=64)
    wing_id: str = Field(default="", max_length=128)
    room_id: str = Field(default="", max_length=256)
    preview: str = Field(default="", max_length=4096)


class MemoryDayView(BaseModel):
    """What it wrote down since a moment the client named."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"] = "1"
    #: Echoed back. A client that asked for "since my last visit" and got a list
    #: with no window could not tell a quiet day from an answer to a different
    #: question.
    since: str = Field(min_length=1, max_length=64)
    entries: list[MemoryEntryView]
    entry_count: int = Field(ge=0)
    #: More inside the window than this page holds. Not the same as
    #: ``truncated``: one is about this answer, the other about how much of the
    #: memory the Host was willing to read.
    more_in_window: bool
    #: Held no usable time, so in no day's list. A number rather than a silence:
    #: someone whose entry never appears should be able to find out why.
    undated_count: int = Field(ge=0)
    truncated: bool


class MemoryExportRecordView(BaseModel):
    """One memory, whole."""

    model_config = ConfigDict(extra="forbid")

    entry_id: str = Field(min_length=1, max_length=128)
    #: Empty when nothing on the record gives a time. Shown at the end of the
    #: file rather than dropped from it: this is the copy.
    recorded_at: str = Field(default="", max_length=64)
    recorded_at_source: str = Field(default="", max_length=64)
    wing_id: str = Field(default="", max_length=128)
    room_id: str = Field(default="", max_length=256)
    memory_type: str = Field(default="", max_length=64)
    #: Required, unlike the fields above it: a copy whose text may be missing is
    #: not a copy.
    value: str = Field(max_length=65536)


class MemoryCopyView(BaseModel):
    """Everything my Eidolon remembers about me, in a form I can keep.

    Not the Host's backup. That one is the palace — vectors and ledgers and the
    encoder they were built with — and exists so a lost disk is survivable; only
    the operator tool ever holds it. This one exists so I am not locked in.
    """

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"] = "1"
    #: Two copies of the same memory differ. Without this, a file I saved months
    #: ago is indistinguishable from one I saved today.
    taken_at: str = Field(min_length=1, max_length=64)
    records: list[MemoryExportRecordView]
    record_count: int = Field(ge=0)
    #: In the file and carrying no date. Counted so I can see why, rather than
    #: wondering what happened to them.
    undated_count: int = Field(ge=0)
    #: The Host stopped reading before the end of my memory. What is here is
    #: real; it is not all of it.
    truncated: bool


class RevokedSessionsView(BaseModel):
    """When every device was signed out."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"] = "1"
    #: The instant the Host compares against. Anything a device was using before
    #: it stops working; anything it gets afterwards is fine, which is why this
    #: is something I can do and then keep using my Eidolon.
    revoked_at: str = Field(min_length=1, max_length=64)


class ConversationView(BaseModel):
    """One time we talked, and what it was called."""

    model_config = ConfigDict(extra="forbid")

    conversation_id: str = Field(min_length=1, max_length=64)
    #: Empty when nothing named it, and it stays empty: a title made up here
    #: would be a screen summarising my conversation for me.
    title: str = Field(default="", max_length=512)
    started_at: str = Field(default="", max_length=64)
    updated_at: str = Field(default="", max_length=64)
    #: Absent while it is still open, which is not the same as ended at a time
    #: nobody recorded.
    ended_at: str | None = Field(default=None, max_length=64)


class ConversationPageView(BaseModel):
    """When this Eidolon and I talked. Not what was said — that is per turn, and
    the Host keeps no words on these rows."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"] = "1"
    companion_id: str = Field(min_length=1, max_length=64)
    conversations: list[ConversationView]
    #: Opaque: sent back as received to ask for the next page.
    next_cursor: str | None = Field(default=None, max_length=256)


class SpokenMessageView(BaseModel):
    """One thing said, by me or by it."""

    model_config = ConfigDict(extra="forbid")

    role: str = Field(min_length=1, max_length=32)
    text: str = Field(default="", max_length=1_048_576)


class TranscriptTurnView(BaseModel):
    """One exchange."""

    model_config = ConfigDict(extra="forbid")

    turn_id: str = Field(min_length=1, max_length=64)
    started_at: str = Field(default="", max_length=64)
    #: Absent while it was still going — what a dropped connection looks like
    #: afterwards.
    finished_at: str | None = Field(default=None, max_length=64)
    status: str = Field(default="", max_length=32)
    messages: list[SpokenMessageView]


class TranscriptView(BaseModel):
    """What was said in one conversation.

    Only what I said and what it said. What tools it called to get there is how
    the answer was reached, not the conversation.
    """

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"] = "1"
    conversation_id: str = Field(min_length=1, max_length=64)
    #: Newest turn first, as the Host answers. Reading order is this app's
    #: decision.
    turns: list[TranscriptTurnView]
    next_cursor: str | None = Field(default=None, max_length=256)


class TaskView(BaseModel):
    """Something I asked it to do, and how far it has got."""

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1, max_length=64)
    #: The Host's word for where this task is. Not an enum here on purpose: the
    #: runtime owns the vocabulary, and a state this app has not heard of must
    #: not make the page unopenable.
    status: str = Field(min_length=1, max_length=32)
    asked: str = Field(default="", max_length=8192)
    kind: str = Field(default="", max_length=64)
    urgency: str = Field(default="", max_length=32)
    expected_output: str = Field(default="", max_length=4096)
    #: What it has said about its own progress. Empty until it says something —
    #: which is different from being stuck.
    progress: str = Field(default="", max_length=8192)
    #: The answer, when there is one. A task can finish without producing one.
    result: str = Field(default="", max_length=65536)
    #: Why it stopped badly. A code for matching, a message for reading.
    error_code: str = Field(default="", max_length=128)
    error_message: str = Field(default="", max_length=4096)
    created_at: str = Field(default="", max_length=64)
    updated_at: str = Field(default="", max_length=64)
    completed_at: str | None = Field(default=None, max_length=64)


class TaskPageView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"] = "1"
    companion_id: str = Field(min_length=1, max_length=64)
    tasks: list[TaskView]
    next_cursor: str | None = Field(default=None, max_length=256)


class PersonaChapterView(BaseModel):
    """One thing my Eidolon has been.

    No version, no hash, no realizer: those are how a Companion is built, and
    what I wonder is when it changed and what changed.
    """

    model_config = ConfigDict(extra="forbid")

    chapter_id: str = Field(min_length=1, max_length=64)
    changed_at: str = Field(min_length=1, max_length=64)
    #: In the words recorded with the change. Empty when nothing was written
    #: down, and it stays empty — a sentence about who my Eidolon became is not
    #: something a screen may compose on its behalf.
    what_changed: str = Field(default="", max_length=4096)
    #: Set when this chapter exists because someone went back to an earlier one.
    restored_from: int | None = None
    is_current: bool = False


class PersonaHistoryView(BaseModel):
    """What my Eidolon has been, newest first."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"] = "1"
    companion_id: str = Field(min_length=1, max_length=64)
    chapters: list[PersonaChapterView]


class PersonaRestoreRequest(BaseModel):
    """Which chapter to go back to."""

    model_config = ConfigDict(extra="forbid")

    chapter_id: str = Field(min_length=1, max_length=64)


class RecollectionView(BaseModel):
    """One thing my Eidolon remembers, as I read it.

    Not the stored record. What memory holds carries wings, rooms, scores and
    provenance — that is how it found something, not what it remembers, and I
    asked the second question.
    """

    model_config = ConfigDict(extra="forbid")

    text: str = Field(default="", max_length=8192)
    #: Absent when memory does not know. Left absent rather than filled in with
    #: the time I asked.
    remembered_at: str | None = Field(default=None, max_length=64)


class RecollectionsView(BaseModel):
    """What it remembers about what I asked."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"] = "1"
    #: Echoed back: an empty answer with no question attached cannot be told
    #: apart from an answer to a different one.
    query: str = Field(min_length=1, max_length=256)
    recollections: list[RecollectionView]


class MemoryAudienceRequest(BaseModel):
    """Which of my Eidolons this memory is for."""

    model_config = ConfigDict(extra="forbid")

    #: Absent or empty means all of them again. Said as an absence rather than a
    #: word, so naming an Eidolon and naming none can never be confused.
    companion_id: str = Field(default="", max_length=128)


class MemoryAudienceView(BaseModel):
    """Where that memory now belongs, and whether it has taken effect yet."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"] = "1"
    entry_id: str = Field(min_length=1, max_length=128)
    #: Empty means every Eidolon may recall it again.
    companion_id: str = Field(default="", max_length=128)
    #: ``applied`` when the Host has already done it; anything else means the
    #: request is durable and still on its way. The client must not say 已经
    #: unless it reads the first.
    status: str = Field(min_length=1, max_length=64)


class ForgetTargetRequest(BaseModel):
    """What to forget, in the person's own words."""

    model_config = ConfigDict(extra="forbid")

    target: str = Field(min_length=1, max_length=512)
    #: ``delete`` removes it; ``archive`` puts it beyond recall but keeps it.
    #: Defaulted to the reversible-sounding one being *absent*: a client that
    #: does not say is asking for what the word "forget" means to a person.
    action: Literal["delete", "archive"] = "delete"


class ForgetConfirmRequest(BaseModel):
    """The token the preview handed back, unchanged."""

    model_config = ConfigDict(extra="forbid")

    #: Opaque to every layer above the memory realm. It carries the exact
    #: entries the person saw, which is what makes confirming safe; a client
    #: that built one itself would be confirming something nobody looked at.
    confirmation_token: str = Field(min_length=1, max_length=4096)


class ForgetEntryView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_id: str = Field(min_length=1, max_length=128)
    preview: str = Field(default="", max_length=4096)
    #: 1.0 is an exact match. Lower means the Host is guessing, and a client
    #: should show that differently rather than presenting a guess as the answer.
    score: float = Field(ge=0.0, le=1.0)


class ForgetProposalView(BaseModel):
    """What would go, before anything goes.

    ``status`` distinguishes three answers a client must handle differently:
    ``preview`` (here is what I found), ``not_found`` (nothing matched those
    words), ``too_broad`` (too much matched to show, so nothing is offered).
    Collapsing the last two into an empty list would leave a person unable to
    tell "you never told me that" from "say which one".
    """

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"] = "1"
    status: Literal["preview", "not_found", "too_broad"]
    target: str = Field(min_length=1, max_length=512)
    action: str | None = Field(default=None, max_length=16)
    entries: list[ForgetEntryView]
    #: True when the match was inexact or hit more than one thing. A client must
    #: ask again rather than treating a guess as an instruction.
    needs_confirmation: bool
    #: Absent when there is nothing to confirm. A client cannot offer the button.
    confirmation_token: str | None = Field(default=None, max_length=4096)
    #: Unix seconds. The preview expires; a decision made ten minutes ago is not
    #: a decision about now.
    expires_at: int | None = None
    detail: str = Field(default="", max_length=1024)


class ForgetResultView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"] = "1"
    action: str = Field(min_length=1, max_length=16)
    target: str = Field(min_length=1, max_length=512)
    entry_count: int = Field(ge=0)
    #: The Host's word for where the change got to. Publishing is durable and
    #: applying is a projection that may still be running, so a client must not
    #: read anything other than the exact status as "done".
    status: str = Field(min_length=1, max_length=64)


class ControllerView(BaseModel):
    """One phone that may manage this Host.

    No public key. A key is how the Host recognises a phone, not how a person
    does — and handing every controller's key to every other controller is
    spending authority material to draw a list. The fingerprint is what a person
    is shown when pairing, so it is what is shown here.
    """

    model_config = ConfigDict(extra="forbid")

    controller_id: str = Field(min_length=1, max_length=64)
    #: What the phone called itself when it claimed this Host.
    display_name: str = Field(default="", max_length=128)
    platform: str = Field(default="", max_length=64)
    role: str = Field(min_length=1, max_length=32)
    #: Short, and shown when two phones are hard to tell apart.
    fingerprint: str = Field(default="", max_length=128)
    claimed_at: str = Field(min_length=1, max_length=64)
    #: Whether this is the phone asking. A property of *this answer*, computed
    #: from the session that made it — never stored, so two of them cannot
    #: disagree. A list that could not say it would leave a person guessing
    #: which row is the phone in their hand before revoking one.
    is_you: bool


class ControllersView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"] = "1"
    controllers: list[ControllerView]


class ControllerInvitationRequest(BaseModel):
    """Open a window in which one more phone may claim this Host."""

    model_config = ConfigDict(extra="forbid")

    #: How long the window stays open. The Host decides the bounds and refuses
    #: what it will not do, so nothing is restated here.
    ttl_seconds: int | None = Field(default=None, ge=1)


class ControllerInvitationView(BaseModel):
    """A one-time code, and when it stops working.

    The code is a secret with a deadline, so the deadline travels with it: a
    screen that showed the code without saying when it dies would let someone
    read it out five minutes too late and think the Host was broken.
    """

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"] = "1"
    setup_code: str = Field(min_length=1, max_length=64)
    expires_at: str = Field(min_length=1, max_length=64)


@runtime_checkable
class ControllerDirectoryPort(Protocol):
    """The Host's own trust root, as this surface asks it.

    Deliberately *not* behind the management backend. That boundary exists so
    the LAN-facing process holds no authority credential (plan §3.4.1), and the
    three things here are not an authority's data: they are this Host's own
    grant record, reached over the control socket this process already
    authenticates every request against. Routing them through the other process
    would add a hop that buys nothing and a second place to get "who is asking"
    wrong.
    """

    async def list_controllers(self, controller_id: str) -> dict: ...

    async def invite_controller(
        self, controller_id: str, ttl_seconds: int | None
    ) -> dict: ...

    async def revoke_controller(self, controller_id: str, target_id: str) -> dict: ...


class ManagementBackendPort(Protocol):
    """What this router needs from the process that holds the credentials."""

    async def context(self, *, owner_id: str) -> dict: ...

    async def roster(self, *, owner_id: str, cursor: str | None) -> dict: ...

    async def companion(self, *, owner_id: str, companion_id: str) -> dict: ...

    async def set_default_companion(
        self, *, owner_id: str, companion_id: str, expected_revision: int
    ) -> dict: ...

    async def create_companion(
        self,
        *,
        owner_id: str,
        operation_id: str,
        display_name: str,
        kind: str,
    ) -> dict: ...

    async def companion_face_state(
        self, *, owner_id: str, companion_id: str
    ) -> dict: ...

    async def companion_face(
        self, *, owner_id: str, companion_id: str, known_etag: str | None
    ) -> tuple[int, bytes, str | None]: ...

    async def set_companion_face(
        self, *, owner_id: str, companion_id: str, face: bytes
    ) -> dict: ...

    async def clear_companion_face(
        self, *, owner_id: str, companion_id: str
    ) -> dict: ...

    async def rename_companion(
        self, *, owner_id: str, companion_id: str, display_name: str
    ) -> dict: ...

    async def rename_owner(self, *, owner_id: str, display_name: str) -> dict: ...

    async def set_companion_lifecycle(
        self,
        *,
        owner_id: str,
        companion_id: str,
        lifecycle_state: str,
        replacement_companion_id: str | None,
        expected_revision: int | None,
    ) -> dict: ...

    async def memory_library(
        self, *, owner_id: str, companion_id: str | None
    ) -> dict: ...

    async def memory_entries(
        self,
        *,
        owner_id: str,
        since: str,
        limit: int | None,
        companion_id: str | None,
    ) -> dict: ...

    async def memory_export(
        self, *, owner_id: str, companion_id: str | None
    ) -> dict: ...

    async def revoke_runtime_sessions(self, *, owner_id: str) -> dict: ...

    async def conversations(
        self,
        *,
        owner_id: str,
        companion_id: str,
        limit: int | None,
        cursor: str | None,
    ) -> dict: ...

    async def transcript(
        self,
        *,
        owner_id: str,
        companion_id: str,
        conversation_id: str,
        limit: int | None,
        cursor: str | None,
    ) -> dict: ...

    async def tasks(
        self,
        *,
        owner_id: str,
        companion_id: str,
        limit: int | None,
        status: str | None,
        cursor: str | None,
    ) -> dict: ...

    async def task(
        self, *, owner_id: str, companion_id: str, task_id: str
    ) -> dict: ...

    async def task_action(
        self, *, owner_id: str, companion_id: str, task_id: str, action: str
    ) -> dict: ...

    async def persona_history(
        self, *, owner_id: str, companion_id: str
    ) -> dict: ...

    async def restore_persona(
        self, *, owner_id: str, companion_id: str, chapter_id: str
    ) -> dict: ...

    async def recollections(
        self, *, owner_id: str, query: str, limit: int, companion_id: str | None
    ) -> dict: ...

    async def assign_memory_audience(
        self, *, owner_id: str, entry_id: str, companion_id: str
    ) -> dict: ...

    async def forget_preview(
        self, *, owner_id: str, target: str, action: str
    ) -> dict: ...

    async def forget_confirm(
        self, *, owner_id: str, confirmation_token: str
    ) -> dict: ...


def _task_page_view(answer: dict) -> TaskPageView:
    return TaskPageView(
        companion_id=answer["companion_id"],
        tasks=[TaskView(**row) for row in answer["tasks"]],
        next_cursor=answer.get("next_cursor"),
    )


def _persona_history_view(answer: dict) -> PersonaHistoryView:
    return PersonaHistoryView(
        companion_id=answer["companion_id"],
        chapters=[
            PersonaChapterView(**chapter) for chapter in answer["chapters"]
        ],
    )


def register_management_routes(
    app,
    *,
    backend: ManagementBackendPort,
    authenticated_owner,
    controllers: ControllerDirectoryPort,
    authenticated_controller_id,
) -> None:
    """Mount the public management surface.

    ``authenticated_owner`` is supplied by the composition root: it verifies the
    Controller session and returns the Owner bound to it. Injected rather than
    imported so this module cannot reach for a different way to decide scope.
    """

    router = APIRouter(prefix=MANAGEMENT_PREFIX, tags=["management"])

    @router.get("/context", response_model=ManagementContextView)
    async def get_context(
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> ManagementContextView:
        owner_id = await authenticated_owner(authorization)
        try:
            answer = await backend.context(owner_id=owner_id)
        except ManagementBackendError as exc:
            raise _refused(exc) from exc
        return ManagementContextView(
            owner=OwnerContextView(
                owner_id=answer["owner_id"],
                display_name=answer["owner_display_name"],
                revision=answer["owner_revision"],
            ),
            default_companion_id=answer["default_companion_id"],
            capabilities=answer["capabilities"],
            limits=answer["limits"],
        )

    @router.get("/controllers", response_model=ControllersView)
    async def list_controllers(
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> ControllersView:
        """Which phones may manage this Host.

        Asked by one that already may — the Host never decides on its own who
        belongs on this list, and it does not answer to a phone that is not on
        it.
        """
        asking = await authenticated_controller_id(authorization)
        answer = await controllers.list_controllers(asking)
        return ControllersView(
            controllers=[
                _controller_view(row, asking=asking)
                for row in answer.get("controllers", [])
            ]
        )

    @router.post("/controllers/invitations", response_model=ControllerInvitationView)
    async def invite_controller(
        payload: ControllerInvitationRequest,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> ControllerInvitationView:
        """Let one more phone in, for a while.

        Asked for by a phone that already holds this Host, so nobody is ever
        added by the Host alone. The answer is a one-time code and the moment it
        stops working.
        """
        asking = await authenticated_controller_id(authorization)
        answer = await controllers.invite_controller(asking, payload.ttl_seconds)
        return ControllerInvitationView(
            setup_code=str(answer["setup_code"]),
            expires_at=str(answer["expires_at"]),
        )

    @router.delete("/controllers/{controller_id}", response_model=ControllerView)
    async def revoke_controller(
        controller_id: str,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> ControllerView:
        """Withdraw a phone's authority over this Host.

        A phone may withdraw its own — what it cannot do is leave the Host with
        nobody, and the Host refuses that rather than this layer guessing at it.
        The answer describes the grant that was withdrawn, so a screen can say
        which phone it just signed out.
        """
        asking = await authenticated_controller_id(authorization)
        answer = await controllers.revoke_controller(asking, controller_id)
        return _controller_view(answer.get("controller", {}), asking=asking)

    @router.get("/companions", response_model=CompanionRosterView)
    async def list_companions(
        cursor: str | None = None,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> CompanionRosterView:
        """This Owner's Eidolons.

        There is no ``owner_id`` parameter and there will not be one: the Owner
        is whoever this session authenticated. ``cursor`` is the one thing a
        client may vary, and it is a value this boundary handed it.
        """
        owner_id = await authenticated_owner(authorization)
        try:
            answer = await backend.roster(owner_id=owner_id, cursor=cursor)
        except ManagementBackendError as exc:
            raise _refused(exc) from exc
        return CompanionRosterView(
            default_companion_id=answer["default_companion_id"],
            companions=[
                CompanionSummaryView(**row) for row in answer["companions"]
            ],
            next_cursor=answer["next_cursor"],
        )

    @router.get("/companions/{companion_id}", response_model=CompanionDetailView)
    async def get_companion(
        companion_id: str,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> CompanionDetailView:
        """One of this Owner's Eidolons.

        The Companion is in the path and the Owner is not expressible, so the
        pair is "this id, for whoever is signed in". One belonging to someone
        else answers 404, not 403: an id that answers differently can be probed
        for existence.
        """
        owner_id = await authenticated_owner(authorization)
        try:
            answer = await backend.companion(
                owner_id=owner_id, companion_id=companion_id
            )
        except ManagementBackendError as exc:
            raise _refused(exc) from exc
        return CompanionDetailView(
            companion_id=answer["companion_id"],
            display_name=answer["display_name"],
            kind=answer["kind"],
            lifecycle_state=answer["lifecycle_state"],
            revision=answer["revision"],
            is_default=answer["is_default"],
        )

    @router.put("/owner/default-companion", response_model=DefaultCompanionView)
    async def put_default_companion(
        payload: DefaultCompanionRequest,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> DefaultCompanionView:
        """Make one of my Eidolons the one that answers by default.

        PUT, so a phone that lost the response can simply ask again. Nothing
        else moves: conversations already running keep the Eidolon they were
        started with, devices stay where they are, and no memory is copied —
        this changes where *new* unaddressed work goes.
        """
        owner_id = await authenticated_owner(authorization)
        try:
            answer = await backend.set_default_companion(
                owner_id=owner_id,
                companion_id=payload.companion_id,
                expected_revision=payload.expected_revision,
            )
        except ManagementBackendError as exc:
            raise _refused(exc) from exc
        return DefaultCompanionView(
            default_companion_id=answer["default_companion_id"]
        )

    @router.put("/companions", response_model=CompanionCreatedView)
    async def create_companion(
        payload: CompanionCreateRequest,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> CompanionCreatedView:
        """Add another Eidolon for whoever is signed in.

        PUT with the client's operation id in the body rather than POST: the
        request states an end ("this operation has produced an Eidolon"), so a
        phone that lost the answer asks again and gets the same one. A POST here
        would make a second Eidolon the normal consequence of a dropped
        response.
        """
        owner_id = await authenticated_owner(authorization)
        try:
            answer = await backend.create_companion(
                owner_id=owner_id,
                operation_id=payload.operation_id,
                display_name=payload.display_name,
                kind=payload.kind,
            )
        except ManagementBackendError as exc:
            raise _refused(exc) from exc
        return CompanionCreatedView(
            companion_id=answer["companion_id"],
            display_name=answer["display_name"],
            kind=answer["kind"],
            lifecycle_state=answer["lifecycle_state"],
            revision=answer["revision"],
            created=answer["created"],
            memory_ready=answer["memory_ready"],
        )

    @router.get(
        "/companions/{companion_id}/face-state", response_model=CompanionFaceView
    )
    async def get_companion_face_state(
        companion_id: str,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> CompanionFaceView:
        """Does this Eidolon have a face, and is it the one I already hold?"""
        owner_id = await authenticated_owner(authorization)
        try:
            answer = await backend.companion_face_state(
                owner_id=owner_id, companion_id=companion_id
            )
        except ManagementBackendError as exc:
            raise _refused(exc) from exc
        return CompanionFaceView(
            companion_id=answer["companion_id"],
            has_face=answer["has_face"],
            sha256=answer["sha256"],
            updated_at=answer["updated_at"],
        )

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
        companion_id: str,
        authorization: str | None = Header(default=None, alias="Authorization"),
        if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    ) -> Response:
        """What this Eidolon looks like.

        A photograph is the one thing on this surface worth not sending twice,
        and this is the hop where that matters — a phone over a house's wifi,
        not a loopback socket. ``If-None-Match`` is answered with 304 and no
        body.
        """
        owner_id = await authenticated_owner(authorization)
        try:
            status_code, content, etag = await backend.companion_face(
                owner_id=owner_id,
                companion_id=companion_id,
                known_etag=if_none_match,
            )
        except ManagementBackendError as exc:
            raise _refused(exc) from exc
        headers = {"ETag": etag} if etag else {}
        if status_code == 304:
            return Response(status_code=304, headers=headers)
        if status_code == 204 or not content:
            return Response(status_code=204)
        return Response(content=content, media_type="image/jpeg", headers=headers)

    @router.put(
        "/companions/{companion_id}/face", response_model=CompanionFaceView
    )
    async def put_companion_face(
        companion_id: str,
        request: Request,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> CompanionFaceView:
        """Give this Eidolon a face; the photograph is the body.

        What may be sent — a JPEG, and not an unbounded one — is decided by the
        authority that stores it rather than restated here, so there is one
        answer to "is this a face" instead of two that can drift apart.
        """
        owner_id = await authenticated_owner(authorization)
        try:
            answer = await backend.set_companion_face(
                owner_id=owner_id,
                companion_id=companion_id,
                face=await request.body(),
            )
        except ManagementBackendError as exc:
            raise _refused(exc) from exc
        return CompanionFaceView(
            companion_id=answer["companion_id"],
            has_face=answer["has_face"],
            sha256=answer["sha256"],
            updated_at=answer["updated_at"],
        )

    @router.delete(
        "/companions/{companion_id}/face", response_model=CompanionFaceView
    )
    async def delete_companion_face(
        companion_id: str,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> CompanionFaceView:
        """Take the face away and leave the Eidolon."""
        owner_id = await authenticated_owner(authorization)
        try:
            answer = await backend.clear_companion_face(
                owner_id=owner_id, companion_id=companion_id
            )
        except ManagementBackendError as exc:
            raise _refused(exc) from exc
        return CompanionFaceView(
            companion_id=answer["companion_id"],
            has_face=answer["has_face"],
            sha256=answer["sha256"],
            updated_at=answer["updated_at"],
        )

    @router.patch(
        "/companions/{companion_id}", response_model=CompanionNameView
    )
    async def patch_companion(
        companion_id: str,
        payload: RenameRequest,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> CompanionNameView:
        """Call this Eidolon something else.

        The name is the Owner's word for it, so nothing here suggests one,
        trims it into a shape, or refuses a name for being unusual. Only a name
        that is not one — blank — is refused.
        """
        owner_id = await authenticated_owner(authorization)
        try:
            answer = await backend.rename_companion(
                owner_id=owner_id,
                companion_id=companion_id,
                display_name=payload.display_name,
            )
        except ManagementBackendError as exc:
            raise _refused(exc) from exc
        return CompanionNameView(
            companion_id=answer["companion_id"],
            display_name=answer["display_name"],
            revision=answer["revision"],
        )

    @router.patch("/owner", response_model=OwnerNameView)
    async def patch_owner(
        payload: RenameRequest,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> OwnerNameView:
        """Change what I am called.

        No Owner in the path, and there will not be one: there is exactly one
        Owner a session can speak for, and letting a client name another would
        create a question this boundary would then have to answer.
        """
        owner_id = await authenticated_owner(authorization)
        try:
            answer = await backend.rename_owner(
                owner_id=owner_id, display_name=payload.display_name
            )
        except ManagementBackendError as exc:
            raise _refused(exc) from exc
        return OwnerNameView(
            owner_id=answer["owner_id"],
            display_name=answer["display_name"],
            revision=answer["revision"],
        )

    @router.put(
        "/companions/{companion_id}/lifecycle",
        response_model=CompanionLifecycleView,
    )
    async def put_companion_lifecycle(
        companion_id: str,
        payload: CompanionLifecycleRequest,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> CompanionLifecycleView:
        """Put one of my Eidolons away, or bring it back.

        Putting away stops new conversations from reaching it — the Host will not
        hand out a runtime snapshot for an Eidolon that is not active — and keeps
        everything it remembers. Nothing is deleted, and bringing it back is the
        same request with the other state.

        Bringing one back does not make it the one that answers again. That is a
        separate thing the Owner said, and reversing it quietly would undo a
        decision they made.
        """
        owner_id = await authenticated_owner(authorization)
        try:
            answer = await backend.set_companion_lifecycle(
                owner_id=owner_id,
                companion_id=companion_id,
                lifecycle_state=payload.lifecycle_state,
                replacement_companion_id=payload.replacement_companion_id,
                expected_revision=payload.expected_revision,
            )
        except ManagementBackendError as exc:
            raise _refused(exc) from exc
        return CompanionLifecycleView(
            companion_id=answer["companion_id"],
            lifecycle_state=answer["lifecycle_state"],
            revision=answer["revision"],
            default_companion_id=answer["default_companion_id"],
        )

    @router.get("/memory/library", response_model=MemoryLibraryView)
    async def get_memory_library(
        companion_id: str | None = None,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> MemoryLibraryView:
        """What my Eidolon remembers.

        ``companion_id`` names an audience, not a scope: the memory is the
        Owner's and every one of their Eidolons reads it. Naming one adds what
        the Owner told that one in particular; naming none answers with the
        shared layer.
        """
        owner_id = await authenticated_owner(authorization)
        try:
            answer = await backend.memory_library(
                owner_id=owner_id, companion_id=companion_id
            )
        except ManagementBackendError as exc:
            raise _refused(exc) from exc
        return MemoryLibraryView(
            wings=[MemoryWingView(**wing) for wing in answer["wings"]],
            entry_count=answer["entry_count"],
            withheld_count=answer["withheld_count"],
            truncated=answer["truncated"],
        )

    @router.post("/memory/forget/preview", response_model=ForgetProposalView)
    async def preview_forget(
        payload: ForgetTargetRequest,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> ForgetProposalView:
        """Show what forgetting this would remove. Nothing changes here.

        Two steps rather than one because a topic is not a set: the words are
        resolved once, shown, and bound — and the confirm acts on what was
        shown rather than on what the words match by then.
        """
        owner_id = await authenticated_owner(authorization)
        try:
            answer = await backend.forget_preview(
                owner_id=owner_id, target=payload.target, action=payload.action
            )
        except ManagementBackendError as exc:
            raise _refused(exc) from exc
        return ForgetProposalView(
            status=answer["status"],
            target=answer["target"],
            action=answer.get("action"),
            entries=[ForgetEntryView(**entry) for entry in answer["entries"]],
            needs_confirmation=answer["needs_confirmation"],
            confirmation_token=answer.get("confirmation_token"),
            expires_at=answer.get("expires_at"),
            detail=answer.get("detail", ""),
        )

    @router.post("/memory/forget/confirm", response_model=ForgetResultView)
    async def confirm_forget(
        payload: ForgetConfirmRequest,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> ForgetResultView:
        """Forget exactly what the preview showed.

        The token is passed through untouched. No layer above the memory realm
        can read it, and none should be able to: it is what ties this decision
        to the entries the person actually looked at.
        """
        owner_id = await authenticated_owner(authorization)
        try:
            answer = await backend.forget_confirm(
                owner_id=owner_id, confirmation_token=payload.confirmation_token
            )
        except ManagementBackendError as exc:
            raise _refused(exc) from exc
        return ForgetResultView(
            action=answer["action"],
            target=answer["target"],
            entry_count=answer["entry_count"],
            status=answer["status"],
        )

    @router.put(
        "/memory/entries/{entry_id}/audience", response_model=MemoryAudienceView
    )
    async def put_memory_entry_audience(
        entry_id: str,
        payload: MemoryAudienceRequest,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> MemoryAudienceView:
        """只让它记得 — keep this memory between me and one of my Eidolons.

        One step, unlike forgetting. A forget turns words into a set and needs a
        preview to bind exactly what will go; here I am looking at one memory and
        naming it, and nothing becomes unrecallable — the Eidolon I gave it to
        still remembers it, and sending this again with nobody named gives it
        back to all of them.
        """
        owner_id = await authenticated_owner(authorization)
        try:
            answer = await backend.assign_memory_audience(
                owner_id=owner_id,
                entry_id=entry_id,
                companion_id=payload.companion_id,
            )
        except ManagementBackendError as exc:
            raise _refused(exc) from exc
        return MemoryAudienceView(
            entry_id=answer["entry_id"],
            companion_id=answer.get("companion_id", ""),
            status=answer["status"],
        )

    @router.post(
        "/owner/actions/revoke-runtime-sessions",
        response_model=RevokedSessionsView,
    )
    async def post_revoke_runtime_sessions(
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> RevokedSessionsView:
        """让所有设备重新登录 — sign every device out, now.

        The action for a phone that went missing. Every device has to get a new
        session before it can talk to an Eidolon again; they do that on their own,
        so this is recoverable rather than a lockout.

        It does **not** remove any phone's access to managing this Host — that is
        a Controller grant, revoked from the devices screen — and it does not
        unpair anything.
        """
        owner_id = await authenticated_owner(authorization)
        try:
            answer = await backend.revoke_runtime_sessions(owner_id=owner_id)
        except ManagementBackendError as exc:
            raise _refused(exc) from exc
        return RevokedSessionsView(revoked_at=answer["revoked_at"])

    @router.get(
        "/companions/{companion_id}/conversations",
        response_model=ConversationPageView,
    )
    async def get_companion_conversations(
        companion_id: str,
        limit: int | None = Query(default=None, ge=1, le=100),
        cursor: str | None = None,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> ConversationPageView:
        """When this Eidolon and I talked."""
        owner_id = await authenticated_owner(authorization)
        try:
            answer = await backend.conversations(
                owner_id=owner_id,
                companion_id=companion_id,
                limit=limit,
                cursor=cursor,
            )
        except ManagementBackendError as exc:
            raise _refused(exc) from exc
        return ConversationPageView(
            companion_id=answer["companion_id"],
            conversations=[
                ConversationView(**row) for row in answer["conversations"]
            ],
            next_cursor=answer.get("next_cursor"),
        )

    @router.get(
        "/companions/{companion_id}/conversations/{conversation_id}/turns",
        response_model=TranscriptView,
    )
    async def get_conversation_transcript(
        companion_id: str,
        conversation_id: str,
        limit: int | None = Query(default=None, ge=1, le=100),
        cursor: str | None = None,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> TranscriptView:
        """What was said that time.

        A page at a time, newest first, so "看更早的" walks back through it.
        """
        owner_id = await authenticated_owner(authorization)
        try:
            answer = await backend.transcript(
                owner_id=owner_id,
                companion_id=companion_id,
                conversation_id=conversation_id,
                limit=limit,
                cursor=cursor,
            )
        except ManagementBackendError as exc:
            raise _refused(exc) from exc
        return TranscriptView(
            conversation_id=answer["conversation_id"],
            turns=[
                TranscriptTurnView(
                    turn_id=turn["turn_id"],
                    started_at=turn["started_at"],
                    finished_at=turn["finished_at"],
                    status=turn["status"],
                    messages=[
                        SpokenMessageView(**message) for message in turn["messages"]
                    ],
                )
                for turn in answer["turns"]
            ],
            next_cursor=answer.get("next_cursor"),
        )

    @router.get("/companions/{companion_id}/tasks", response_model=TaskPageView)
    async def get_companion_tasks(
        companion_id: str,
        limit: int | None = Query(default=None, ge=1, le=100),
        status: str | None = None,
        cursor: str | None = None,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> TaskPageView:
        """What I asked it to do, and how far it has got."""
        owner_id = await authenticated_owner(authorization)
        try:
            answer = await backend.tasks(
                owner_id=owner_id,
                companion_id=companion_id,
                limit=limit,
                status=status,
                cursor=cursor,
            )
        except ManagementBackendError as exc:
            raise _refused(exc) from exc
        return _task_page_view(answer)

    @router.get(
        "/companions/{companion_id}/tasks/{task_id}", response_model=TaskView
    )
    async def get_companion_task(
        companion_id: str,
        task_id: str,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> TaskView:
        owner_id = await authenticated_owner(authorization)
        try:
            answer = await backend.task(
                owner_id=owner_id, companion_id=companion_id, task_id=task_id
            )
        except ManagementBackendError as exc:
            raise _refused(exc) from exc
        return TaskView(**answer)

    @router.post(
        "/companions/{companion_id}/tasks/{task_id}/cancel", response_model=TaskView
    )
    async def post_companion_task_cancel(
        companion_id: str,
        task_id: str,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> TaskView:
        """别做了 — stop it.

        What comes back is what the Host says the task became, not what this app
        hoped: the task's states belong to the runtime that runs it, and it may
        refuse because the thing finished while I was looking at the page.
        """
        return await _task_action(companion_id, task_id, "cancel", authorization)

    @router.post(
        "/companions/{companion_id}/tasks/{task_id}/retry", response_model=TaskView
    )
    async def post_companion_task_retry(
        companion_id: str,
        task_id: str,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> TaskView:
        """再试一次 — ask for it again. The Host decides whether it can."""

        return await _task_action(companion_id, task_id, "retry", authorization)

    async def _task_action(
        companion_id: str,
        task_id: str,
        action: str,
        authorization: str | None,
    ) -> TaskView:
        owner_id = await authenticated_owner(authorization)
        try:
            answer = await backend.task_action(
                owner_id=owner_id,
                companion_id=companion_id,
                task_id=task_id,
                action=action,
            )
        except ManagementBackendError as exc:
            raise _refused(exc) from exc
        return TaskView(**answer)

    @router.get(
        "/companions/{companion_id}/persona-history",
        response_model=PersonaHistoryView,
    )
    async def get_persona_history(
        companion_id: str,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> PersonaHistoryView:
        """What this Eidolon has been.

        A record, not a settings screen: every chapter is something it actually
        was, and there is no proposal queue here — a Companion considering a
        change has not changed.
        """
        owner_id = await authenticated_owner(authorization)
        try:
            answer = await backend.persona_history(
                owner_id=owner_id, companion_id=companion_id
            )
        except ManagementBackendError as exc:
            raise _refused(exc) from exc
        return _persona_history_view(answer)

    @router.put(
        "/companions/{companion_id}/persona-restorations",
        response_model=PersonaHistoryView,
    )
    async def put_persona_restoration(
        companion_id: str,
        payload: PersonaRestoreRequest,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> PersonaHistoryView:
        """Make it the way it was then.

        Going back appends a chapter rather than rewinding, so the record keeps
        the months in between and says when I went back. The answer is the whole
        history, because what I want to see afterwards is where that leaves it.
        """
        owner_id = await authenticated_owner(authorization)
        try:
            answer = await backend.restore_persona(
                owner_id=owner_id,
                companion_id=companion_id,
                chapter_id=payload.chapter_id,
            )
        except ManagementBackendError as exc:
            raise _refused(exc) from exc
        return _persona_history_view(answer)

    @router.get("/memory/recollections", response_model=RecollectionsView)
    async def get_memory_recollections(
        # Bounded here, exactly as the deleted ``/api/local/v1/recollections``
        # bounded them: a question is required and an unbounded answer is refused
        # rather than passed down to memory. Carried across the migration on
        # purpose — a limit that quietly became unlimited is the sort of thing a
        # move like this loses.
        q: str = Query(min_length=1, max_length=256),
        limit: int = Query(default=10, ge=1, le=50),
        companion_id: str | None = None,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> RecollectionsView:
        """"你还记得…吗" — the question a person actually arrives with.

        Owner-scoped by the session, like every other read here: there is one
        memory a session can ask about, so none is named.
        """
        owner_id = await authenticated_owner(authorization)
        try:
            answer = await backend.recollections(
                owner_id=owner_id,
                query=q,
                limit=limit,
                companion_id=companion_id,
            )
        except ManagementBackendError as exc:
            raise _refused(exc) from exc
        return RecollectionsView(
            query=answer["query"],
            recollections=[
                RecollectionView(**entry) for entry in answer["recollections"]
            ],
        )

    @router.get("/memory/export", response_model=MemoryCopyView)
    async def get_memory_export(
        companion_id: str | None = None,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> MemoryCopyView:
        """A copy of everything my Eidolon remembers that I can see.

        The one read here that shortens nothing: the library and the day list are
        pages I scroll, and this is a file I keep. Its two counts are what keep it
        honest — how many memories carry no date, and whether the Host stopped
        reading before the end.
        """
        owner_id = await authenticated_owner(authorization)
        try:
            answer = await backend.memory_export(
                owner_id=owner_id, companion_id=companion_id
            )
        except ManagementBackendError as exc:
            raise _refused(exc) from exc
        return MemoryCopyView(
            taken_at=answer["taken_at"],
            records=[
                MemoryExportRecordView(**record) for record in answer["records"]
            ],
            record_count=answer["record_count"],
            undated_count=answer["undated_count"],
            truncated=answer["truncated"],
        )

    @router.get("/memory/entries", response_model=MemoryDayView)
    async def get_memory_entries(
        since: str,
        limit: int | None = None,
        companion_id: str | None = None,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> MemoryDayView:
        """What it has written down since a moment I name.

        ``since`` is required. My "today" depends on where I am, and the Host
        does not know that — so the client says when the day started rather than
        the Host guessing and being wrong by up to a day.
        """
        owner_id = await authenticated_owner(authorization)
        try:
            answer = await backend.memory_entries(
                owner_id=owner_id,
                since=since,
                limit=limit,
                companion_id=companion_id,
            )
        except ManagementBackendError as exc:
            raise _refused(exc) from exc
        return MemoryDayView(
            since=answer["since"],
            entries=[MemoryEntryView(**entry) for entry in answer["entries"]],
            entry_count=answer["entry_count"],
            more_in_window=answer["more_in_window"],
            undated_count=answer["undated_count"],
            truncated=answer["truncated"],
        )

    app.include_router(router)


ManagementContextView.model_rebuild()
