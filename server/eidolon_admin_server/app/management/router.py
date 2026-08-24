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
            owner_id=owner_id, owners=request.app.state.control_plane.data
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
