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

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from eidolon_admin_server.app.control_plane.errors import AuthorityFailure
from eidolon_admin_server.app.management.context import read_context
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
