"""The internal ABI Local API calls, separate from the operator plane.

Its own prefix rather than another branch of ``/api/control-plane/v1``, because
that path family is already serving two audiences at once — a browser holding an
operator credential and a loopback service holding a service token — and adding
a third meaning to it is how the confusion this plan exists to remove got here.

Everything under here requires the Local API service credential. It is an
internal ABI: no browser reaches it, and the Owner it acts for arrives as an
argument from a boundary that authenticated a Controller, never as something a
caller may choose.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from eidolon_admin_server.app.control_plane.errors import AuthorityFailure
from eidolon_admin_server.app.management.context import read_context
from eidolon_admin_server.app.management.creation import create_companion
from eidolon_admin_server.app.management.forgetting import apply_forget, propose_forget
from eidolon_admin_server.app.management.audience import assign_memory_audience
from eidolon_admin_server.app.management.memory import read_copy, read_day, read_library
from eidolon_admin_server.app.management.recollecting import recall
from eidolon_admin_server.app.management.roster import (
    read_companion,
    read_roster,
    set_default_companion,
)
from eidolon_admin_server.app.service_auth import require_local_api_credential

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


class CompanionRosterInternal(BaseModel):
    """A page of the Owner's roster, projected for Local API."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"] = "1"
    operation: Literal["companion.roster"] = "companion.roster"
    owner_id: str = Field(min_length=1, max_length=64)
    #: Named once for the page. A per-row flag would let two rows claim it.
    default_companion_id: str | None = Field(default=None, max_length=64)
    companions: list[CompanionSummaryInternal]
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
    wings: list[MemoryWingInternal]
    entry_count: int = Field(ge=0)
    withheld_count: int = Field(ge=0)
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


class MemoryAudienceInternal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"] = "1"
    operation: Literal["memory.audience"] = "memory.audience"
    entry_id: str = Field(min_length=1, max_length=128)
    #: Empty means the Owner layer. The audience token itself does not travel:
    #: nothing above needs to know it spells ``companion:<id>``.
    companion_id: str = Field(default="", max_length=128)
    status: str = Field(min_length=1, max_length=64)


class MemoryAudienceRequestInternal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Absent or empty gives the memory back to every Companion. Said as an
    #: absence rather than a magic string so no client can name a Companion
    #: called "owner" and mean the Owner layer.
    companion_id: str = Field(default="", max_length=128)


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
    try:
        context = await read_context(
            owner_id=owner_id,
            # The Owner aggregate is the workspace authority's, not the
            # Companion authority's. Reaching for ``.data`` here raised
            # AttributeError at runtime and no test noticed, because every test
            # injected its own reader. test_management_composition.py now
            # asserts the composed service satisfies these Protocols.
            owners=request.app.state.control_plane.workspace,
        )
    except AuthorityFailure as exc:
        raise HTTPException(
            status_code=exc.status_code, detail=exc.to_wire().model_dump()
        ) from exc
    return ManagementContextInternal(
        owner_id=context.owner_id,
        owner_display_name=context.owner_display_name,
        owner_revision=context.owner_revision,
        default_companion_id=context.default_companion_id,
        capabilities=context.capabilities,
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
    try:
        roster = await read_roster(
            owner_id=owner_id,
            companions=request.app.state.control_plane.data,
            cursor=cursor,
        )
    except AuthorityFailure as exc:
        raise HTTPException(
            status_code=exc.status_code, detail=exc.to_wire().model_dump()
        ) from exc
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
            )
            for row in roster.companions
        ],
        next_cursor=roster.next_cursor,
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
    try:
        detail = await read_companion(
            owner_id=owner_id,
            companion_id=companion_id,
            companions=request.app.state.control_plane.data,
            owners=request.app.state.control_plane.workspace,
        )
    except AuthorityFailure as exc:
        raise HTTPException(
            status_code=exc.status_code, detail=exc.to_wire().model_dump()
        ) from exc
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
    try:
        default_companion_id = await set_default_companion(
            owner_id=owner_id,
            companion_id=payload.companion_id,
            expected_revision=payload.expected_revision,
            owners=request.app.state.control_plane.workspace,
        )
    except AuthorityFailure as exc:
        raise HTTPException(
            status_code=exc.status_code, detail=exc.to_wire().model_dump()
        ) from exc
    return DefaultCompanionResponseInternal(
        default_companion_id=default_companion_id
    )


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
    try:
        created = await create_companion(
            owner_id=owner_id,
            operation_id=operation_id,
            display_name=payload.display_name,
            kind=payload.kind,
            companions=control_plane.workspace,
            memory=getattr(control_plane, "memory_supervisor", None),
        )
    except AuthorityFailure as exc:
        raise HTTPException(
            status_code=exc.status_code, detail=exc.to_wire().model_dump()
        ) from exc
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

    ``companion_id`` is an audience, not a scope: memory belongs to the Owner
    and every one of their Companions reads it. Naming one adds that Companion's
    own layer; naming none answers with the Owner layer, which is the safe
    direction for a caller that did not say.
    """
    try:
        library = await read_library(
            owner_id=owner_id,
            companion_id=companion_id,
            memory=request.app.state.control_plane.memory,
        )
    except AuthorityFailure as exc:
        raise HTTPException(
            status_code=exc.status_code, detail=exc.to_wire().model_dump()
        ) from exc
    return MemoryLibraryInternal(
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


@router.post("/memory/forget/preview", response_model=ForgetProposalInternal)
async def post_forget_preview(
    request: Request,
    owner_id: str,
    payload: ForgetTargetInternal,
) -> ForgetProposalInternal:
    """What forgetting this would remove, without removing it."""
    try:
        proposal = await propose_forget(
            owner_id=owner_id,
            target=payload.target,
            action=payload.action,
            memory=request.app.state.control_plane.memory,
        )
    except AuthorityFailure as exc:
        raise HTTPException(
            status_code=exc.status_code, detail=exc.to_wire().model_dump()
        ) from exc
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
    try:
        result = await apply_forget(
            owner_id=owner_id,
            confirmation_token=payload.confirmation_token,
            memory=request.app.state.control_plane.memory,
        )
    except AuthorityFailure as exc:
        raise HTTPException(
            status_code=exc.status_code, detail=exc.to_wire().model_dump()
        ) from exc
    return ForgetResultInternal(
        action=result.action,
        target=result.target,
        entry_count=result.entry_count,
        status=result.status,
    )


@router.put("/memory/entries/{entry_id}/audience", response_model=MemoryAudienceInternal)
async def put_memory_entry_audience(
    request: Request,
    entry_id: str,
    owner_id: str,
    payload: MemoryAudienceRequestInternal,
) -> MemoryAudienceInternal:
    """Say which of this Owner's Eidolons one memory belongs to.

    ``PUT``: the body is the desired end state of an exact record, so a client
    that never saw the answer can send it again and nothing happens twice. The
    entry id names a drawer in the realm's store and is not interpreted here —
    the realm refuses anything that is not one of its own.
    """
    try:
        result = await assign_memory_audience(
            owner_id=owner_id,
            entry_id=entry_id,
            companion_id=payload.companion_id or None,
            memory=request.app.state.control_plane.memory,
        )
    except AuthorityFailure as exc:
        raise HTTPException(
            status_code=exc.status_code, detail=exc.to_wire().model_dump()
        ) from exc
    return MemoryAudienceInternal(
        entry_id=result.entry_id,
        companion_id=result.companion_id,
        status=result.status,
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
    try:
        found = await recall(
            owner_id=owner_id,
            query=q,
            limit=limit,
            companion_id=companion_id,
            memory=request.app.state.control_plane.memory,
        )
    except AuthorityFailure as exc:
        raise HTTPException(
            status_code=exc.status_code, detail=exc.to_wire().model_dump()
        ) from exc
    return RecollectionsInternal(
        query=found.query,
        recollections=[
            RecollectionInternal(
                text=entry.text, remembered_at=entry.remembered_at
            )
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
    try:
        copy = await read_copy(
            owner_id=owner_id,
            companion_id=companion_id,
            memory=request.app.state.control_plane.memory,
        )
    except AuthorityFailure as exc:
        raise HTTPException(
            status_code=exc.status_code, detail=exc.to_wire().model_dump()
        ) from exc
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
    try:
        day = await read_day(
            owner_id=owner_id,
            since=since,
            limit=limit,
            companion_id=companion_id,
            memory=request.app.state.control_plane.memory,
        )
    except AuthorityFailure as exc:
        raise HTTPException(
            status_code=exc.status_code, detail=exc.to_wire().model_dump()
        ) from exc
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
