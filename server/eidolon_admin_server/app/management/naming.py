"""What things are called: an Eidolon's name, and its Owner's own.

Two writes that look trivial and are not, for one reason each.

**A Companion is renamed through a route that does not say whose it is.** The
authority's rename is keyed on a Companion alone, so ownership is proved by
asking the owner-scoped Companion route first — the same shape the persona
history uses, and for the same reason: proving it *here* by comparing an
``owner_id`` would put a second adjudicator next to the one that already knows.
Someone else's Companion is a 404, never a 403, so an identifier cannot be
probed for existence.

**An Owner renames only themselves.** There is no owner-scoped rename to prove
anything against, because the Owner *is* the scope: it arrives from the boundary
that authenticated a Controller, and no route above lets a caller name a
different one.

Neither function decides what a name may be. Emptiness is refused at the
boundary a person is actually typing into, and the length the authority accepts
is the authority's.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from eidolon_admin_server.app.control_plane.contracts import (
    CompanionIdentity,
    OwnerIdentity,
)
from eidolon_admin_server.app.management.roster import RosterReader


@runtime_checkable
class CompanionNamer(Protocol):
    async def rename_companion(
        self, companion_id: str, display_name: str
    ) -> CompanionIdentity: ...


@runtime_checkable
class OwnerNamer(Protocol):
    async def rename_owner(self, owner_id: str, display_name: str) -> OwnerIdentity: ...


async def rename_companion(
    *,
    owner_id: str,
    companion_id: str,
    display_name: str,
    companions: RosterReader,
    namer: CompanionNamer,
) -> CompanionIdentity:
    await companions.get_owner_companion(owner_id, companion_id)
    return await namer.rename_companion(companion_id, display_name)


async def rename_owner(
    *,
    owner_id: str,
    display_name: str,
    namer: OwnerNamer,
) -> OwnerIdentity:
    return await namer.rename_owner(owner_id, display_name)
