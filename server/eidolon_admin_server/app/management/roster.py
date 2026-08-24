"""The Owner's roster, as a management client reads it.

Thin on purpose. The authority already answers this question — owner-scoped,
keyset-paged, with the default named once per page — so this layer's whole job
is to *not* add a second opinion:

- it does not sort (the authority's order is creation order, and an order that
  encoded defaultness would be a second place saying which one is default);
- it does not resolve a null default by picking a Companion;
- it does not decode or re-issue the cursor;
- it does not filter archived rows out. A person who archived an Eidolon should
  be able to see that it exists in that state; hiding it here would make the
  roster disagree with the authority about what the Owner has.

What it does do is refuse to let the Owner be chosen by a caller: ``owner_id``
arrives as an argument from a boundary that authenticated a Controller.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from eidolon_admin_server.app.control_plane.contracts import (
    CompanionIdentity,
    CompanionRosterPage,
)


@runtime_checkable
class RosterReader(Protocol):
    """The two authority calls these reads need."""

    async def list_owner_companions(
        self,
        owner_id: str,
        *,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> CompanionRosterPage: ...

    async def get_owner_companion(
        self,
        owner_id: str,
        companion_id: str,
    ) -> CompanionIdentity: ...


@dataclass(frozen=True, slots=True)
class CompanionRow:
    companion_id: str
    display_name: str
    kind: str
    lifecycle_state: str
    revision: int
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class Roster:
    """One page. ``default_companion_id`` is named here and nowhere per row."""

    owner_id: str
    default_companion_id: str | None
    companions: tuple[CompanionRow, ...]
    next_cursor: str | None


async def read_roster(
    *,
    owner_id: str,
    companions: RosterReader,
    cursor: str | None = None,
) -> Roster:
    page = await companions.list_owner_companions(owner_id, cursor=cursor)
    return Roster(
        owner_id=page.owner_id,
        default_companion_id=page.default_companion_id,
        companions=tuple(
            CompanionRow(
                companion_id=row.companion_id,
                display_name=row.display_name,
                kind=row.kind,
                lifecycle_state=row.lifecycle_state,
                revision=row.revision,
                # ISO 8601 strings, because the wire is JSON and a client that
                # is handed a formatted local time cannot recover the instant.
                created_at=row.created_at.isoformat(),
                updated_at=row.updated_at.isoformat(),
            )
            for row in page.companions
        ),
        next_cursor=page.next_cursor,
    )


@dataclass(frozen=True, slots=True)
class CompanionDetail:
    """One Companion, and whether the Owner's pointer names it.

    ``is_default`` is computed here, from one comparison against the Owner's
    single pointer, and it is a property of *this answer* rather than a stored
    fact about the Companion. That is the difference between a derived view and
    a second authority: nothing writes it, and two of these can never disagree
    because neither is remembered.
    """

    companion_id: str
    display_name: str
    kind: str
    lifecycle_state: str
    revision: int
    is_default: bool


async def read_companion(
    *,
    owner_id: str,
    companion_id: str,
    companions: RosterReader,
    owners,
) -> CompanionDetail:
    """One Companion of this Owner, or an authority 404.

    Both facts come from their own authority: the Companion from the
    owner-scoped Companion route (which proves ownership rather than trusting
    this layer to compare), and "which one is default" from the Owner
    aggregate. This function only compares them.
    """

    identity = await companions.get_owner_companion(owner_id, companion_id)
    owner = await owners.get_owner(owner_id)
    return CompanionDetail(
        companion_id=identity.companion_id,
        display_name=identity.display_name,
        kind=identity.kind,
        lifecycle_state=identity.lifecycle_state,
        revision=identity.revision,
        is_default=owner.default_companion_id == identity.companion_id,
    )
