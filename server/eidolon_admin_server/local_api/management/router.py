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

from typing import Literal, Protocol

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

MANAGEMENT_PREFIX = "/api/management/v1"


class ManagementBackendError(RuntimeError):
    """The backend refused or could not answer; carries the status to relay."""

    def __init__(self, message: str, *, status_code: int = 503) -> None:
        super().__init__(message)
        self.status_code = status_code


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

    async def forget_preview(
        self, *, owner_id: str, target: str, action: str
    ) -> dict: ...

    async def forget_confirm(
        self, *, owner_id: str, confirmation_token: str
    ) -> dict: ...


def register_management_routes(
    app,
    *,
    backend: ManagementBackendPort,
    authenticated_owner,
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
            raise HTTPException(exc.status_code, str(exc)) from exc
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
            raise HTTPException(exc.status_code, str(exc)) from exc
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
            raise HTTPException(exc.status_code, str(exc)) from exc
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
            raise HTTPException(exc.status_code, str(exc)) from exc
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
            raise HTTPException(exc.status_code, str(exc)) from exc
        return CompanionCreatedView(
            companion_id=answer["companion_id"],
            display_name=answer["display_name"],
            kind=answer["kind"],
            lifecycle_state=answer["lifecycle_state"],
            revision=answer["revision"],
            created=answer["created"],
            memory_ready=answer["memory_ready"],
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
            raise HTTPException(exc.status_code, str(exc)) from exc
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
            raise HTTPException(exc.status_code, str(exc)) from exc
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
            raise HTTPException(exc.status_code, str(exc)) from exc
        return ForgetResultView(
            action=answer["action"],
            target=answer["target"],
            entry_count=answer["entry_count"],
            status=answer["status"],
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
            raise HTTPException(exc.status_code, str(exc)) from exc
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
