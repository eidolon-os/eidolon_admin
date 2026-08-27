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
    OwnerRuntimeCompanions,
)


@runtime_checkable
class RuntimeReader(Protocol):
    """Which Companions the runtime is holding, right now."""

    async def runtime_companions(self, *, owner_id: str) -> OwnerRuntimeCompanions: ...


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
    genome_id: str | None = None
    memory_realm_id: str | None = None
    #: Whether the runtime is holding this Companion at this moment, and when it
    #: was last addressed. ``None`` is **unknown** — the runtime could not be
    #: asked — and is not the same as ``False``, which is a real answer meaning
    #: nothing is running for it. A client that renders unknown as "not running"
    #: repeats, in the other direction, the guess this field exists to end.
    running: bool | None = None
    last_active_at: str = ""


@dataclass(frozen=True, slots=True)
class Roster:
    """One page. ``default_companion_id`` is named here and nowhere per row.

    ``runtime_unavailable`` carries why the runtime could not be read, when it
    could not. Lifecycle comes from an authority and runtime from a live
    process, and the second failing must not take the first down with it: a
    person should still see what Eidolons they have when the Agent is restarting.
    """

    owner_id: str
    default_companion_id: str | None
    companions: tuple[CompanionRow, ...]
    next_cursor: str | None
    runtime_unavailable: str = ""


async def read_roster(
    *,
    owner_id: str,
    companions: RosterReader,
    runtime: RuntimeReader | None = None,
    cursor: str | None = None,
) -> Roster:
    """What this Owner has, and which of them are running.

    Two sources, and their failures are not the same size. The authority answers
    what exists — without it there is no roster. The runtime answers what is
    live — without it every row simply says "unknown", because a list of
    somebody's Eidolons is worth showing even when the process that runs them is
    momentarily unreachable.

    Nothing is inferred across the two. In particular the default Companion is
    not treated as the running one: that guess is what this read replaces.
    """

    page = await companions.list_owner_companions(owner_id, cursor=cursor)

    live: dict[str, str] | None = None
    unavailable = ""
    if runtime is None:
        unavailable = "runtime_not_configured"
    else:
        try:
            answer = await runtime.runtime_companions(owner_id=owner_id)
        except Exception as exc:  # noqa: BLE001 - a degraded read, not a failure
            # Deliberately broad, and deliberately not re-raised: this is the
            # one source whose absence costs a column rather than the answer.
            unavailable = _runtime_unavailable(exc)
        else:
            live = {
                row.companion_id: row.last_active_at for row in answer.companions
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
                genome_id=row.current_genome_id,
                memory_realm_id=row.memory_realm_id,
                running=None if live is None else row.companion_id in live,
                last_active_at=(live or {}).get(row.companion_id, ""),
            )
            for row in page.companions
        ),
        next_cursor=page.next_cursor,
        runtime_unavailable=unavailable,
    )


def _runtime_unavailable(error: Exception) -> str:
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
