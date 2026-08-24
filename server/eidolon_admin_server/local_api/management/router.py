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


class ManagementBackendPort(Protocol):
    """What this router needs from the process that holds the credentials."""

    async def context(self, *, owner_id: str) -> dict: ...

    async def roster(self, *, owner_id: str, cursor: str | None) -> dict: ...


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

    app.include_router(router)


ManagementContextView.model_rebuild()
