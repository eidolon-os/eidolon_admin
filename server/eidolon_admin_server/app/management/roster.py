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
    OwnerCompanionActivity,
)


@runtime_checkable
class ActivityReader(Protocol):
    """When each Companion was last spoken to."""

    async def companion_activity(self, *, owner_id: str) -> OwnerCompanionActivity: ...


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
    artwork_id: str | None = None
    genome_id: str | None = None
    memory_realm_id: str | None = None
    #: When this Owner last spoke to it. Empty means one of two things, and the
    #: page says which: with ``activity_unavailable`` empty it means they never
    #: have, and otherwise it means nobody could ask.
    #:
    #: This row used to carry ``running`` beside it — whether the Agent process
    #: happened to hold a live object for this Companion. It was true and it was
    #: meaningless: the next thing said to an Eidolon makes it running, nothing
    #: is wrong while it is not, and there is no button that changes it. What it
    #: produced on a phone was 「未运行」 on every row after a deploy.
    last_active_at: str = ""


@dataclass(frozen=True, slots=True)
class Roster:
    """One page. ``default_companion_id`` is named here and nowhere per row.

    ``activity_unavailable`` carries why the conversation history could not be
    read, when it could not. Lifecycle comes from an authority and history from
    the runtime, and the second failing must not take the first down with it: a
    person should still see what Eidolons they have when the Agent is restarting.
    """

    owner_id: str
    default_companion_id: str | None
    companions: tuple[CompanionRow, ...]
    next_cursor: str | None
    activity_unavailable: str = ""


async def read_roster(
    *,
    owner_id: str,
    companions: RosterReader,
    activity: ActivityReader | None = None,
    cursor: str | None = None,
) -> Roster:
    """What this Owner has, and when they last spoke to each of them.

    Two sources, and their failures are not the same size. The authority answers
    what exists — without it there is no roster. The runtime answers when each
    was last used — without it every row simply carries no time, because a list
    of somebody's Eidolons is worth showing even when the process that keeps the
    conversations is momentarily unreachable.

    Nothing is inferred across the two. In particular the default Companion is
    not treated as the most recently used one: guessing like that, from a routing
    fallback, is what both this read and the one before it exist to replace.
    """

    page = await companions.list_owner_companions(owner_id, cursor=cursor)

    last_spoken: dict[str, str] | None = None
    unavailable = ""
    if activity is None:
        unavailable = "runtime_not_configured"
    else:
        try:
            answer = await activity.companion_activity(owner_id=owner_id)
        except Exception as exc:  # noqa: BLE001 - a degraded read, not a failure
            # Deliberately broad, and deliberately not re-raised: this is the
            # one source whose absence costs a column rather than the answer.
            unavailable = _activity_unavailable(exc)
        else:
            last_spoken = {
                row.companion_id: row.last_conversation_at for row in answer.companions
            }

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
                artwork_id=row.artwork_id,
                genome_id=row.current_genome_id,
                memory_realm_id=row.memory_realm_id,
                last_active_at=(last_spoken or {}).get(row.companion_id, ""),
            )
            for row in page.companions
        ),
        next_cursor=page.next_cursor,
        activity_unavailable=unavailable,
    )


def _activity_unavailable(error: Exception) -> str:
    """Why the runtime could not say, in a word a client can act on.

    A reason rather than a sentence, for the same reason refusals carry codes:
    "the Agent is restarting" and "this Host has no Agent" lead a person to
    different places, and matching on prose is how that distinction gets lost.
    """

    status = getattr(error, "upstream_status", None) or getattr(
        error, "status_code", None
    )
    if status == 503:
        return "runtime_starting"
    return "runtime_unreachable"


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


@runtime_checkable
class DefaultCompanionWriter(Protocol):
    """The one authority call this command needs."""

    async def set_default_companion(
        self,
        owner_id: str,
        *,
        companion_id: str,
        expected_revision: int,
    ): ...


async def set_default_companion(
    *,
    owner_id: str,
    companion_id: str,
    expected_revision: int,
    owners: DefaultCompanionWriter,
) -> str | None:
    """Ask the authority to move the Owner's pointer; return where it now points.

    This layer holds no rule of its own. Whether the Companion is this Owner's,
    whether a guard may be the default, and whether the caller's revision is
    current are all the authority's to answer — and it answers them inside the
    transaction that does the write, which is the only place those checks are
    not a race.
    """

    identity = await owners.set_default_companion(
        owner_id,
        companion_id=companion_id,
        expected_revision=expected_revision,
    )
    return identity.default_companion_id
