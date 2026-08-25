"""Putting an Eidolon away, and bringing it back.

Two actions for a person, four states underneath. The step in between —
``retiring``, where the authority stops handing out runtime snapshots so no new
session can start — is not something this layer knows about: it asks
``companion_lifecycle_path`` for the route and walks whatever comes back. That
is the whole reason the route lives in the SDK next to the transition table. A
projection that spelled out "archiving means retiring first" would be a second
copy of the state machine, and the two would disagree the first time a state is
added between them.

**No journal, and here is why this one does not need one.** Each step is an
idempotent PUT stating a desired end, and the intermediate state is durable and
resumable: an archive interrupted after the first step leaves a Companion
``retiring``, and asking again continues from there rather than starting over.
Nothing here spans two authorities, so there is no point where a crash could
leave two of them disagreeing. The plan's durable operation journal is for the
workflows that *do* — and reaching for it here would be machinery around a
sequence that already recovers by being asked again.

**It refuses nothing itself.** Whether this Companion may be archived, whether
the named replacement can take the role, whether the revision still matches:
every one of those is decided by the authority and relayed, code and all. The
one thing this layer will not do is *pick* a replacement. An Owner whose Eidolon
answers when nobody was named has to say who answers instead — choosing for them
would be this layer deciding something about their life.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from eidolon_sdk.biz.contracts.companion import (
    LIFECYCLE_ACTIVE,
    LIFECYCLE_ARCHIVED,
    LIFECYCLE_RETIRING,
    companion_lifecycle_path,
)

from eidolon_admin_server.app.control_plane.contracts import CompanionLifecycleResult
from eidolon_admin_server.app.management.context import OwnerReader
from eidolon_admin_server.app.management.roster import RosterReader


@runtime_checkable
class CompanionLifecycleWriter(Protocol):
    """The one authority call this needs."""

    async def set_companion_lifecycle(
        self,
        owner_id: str,
        *,
        companion_id: str,
        lifecycle_state: str,
        expected_revision: int | None = None,
        replacement_companion_id: str | None = None,
    ) -> CompanionLifecycleResult: ...


@dataclass(frozen=True, slots=True)
class CompanionLifecycleView:
    """Where this Eidolon is now, and who answers for the Owner.

    Both, because putting away the Companion that answers hands the role over in
    the same breath. A client that had to ask again would be showing a person
    "archived" next to a stale answer to "so who talks to me now".
    """

    companion_id: str
    lifecycle_state: str
    revision: int
    default_companion_id: str | None


async def put_away(
    *,
    owner_id: str,
    companion_id: str,
    replacement_companion_id: str | None = None,
    expected_revision: int | None = None,
    companions: RosterReader,
    owners: OwnerReader,
    lifecycle: CompanionLifecycleWriter,
) -> CompanionLifecycleView:
    """Archive one of this Owner's Eidolons.

    ``replacement_companion_id`` is required only when this is the one that
    answers unaddressed requests, and the authority is what says so — by
    refusing with ``default_replacement_required``, which reaches the client
    intact so it can ask the person the question rather than guessing at it.
    """

    return await _move(
        owner_id=owner_id,
        companion_id=companion_id,
        target=LIFECYCLE_ARCHIVED,
        replacement_companion_id=replacement_companion_id,
        expected_revision=expected_revision,
        companions=companions,
        owners=owners,
        lifecycle=lifecycle,
    )


async def bring_back(
    *,
    owner_id: str,
    companion_id: str,
    expected_revision: int | None = None,
    companions: RosterReader,
    owners: OwnerReader,
    lifecycle: CompanionLifecycleWriter,
) -> CompanionLifecycleView:
    """Make an archived Eidolon answer again.

    It does not take the default role back. Restoring says "this one is here
    again", and who answers when nobody is named is a separate thing the Owner
    said — quietly reversing it would undo a decision they made.
    """

    return await _move(
        owner_id=owner_id,
        companion_id=companion_id,
        target=LIFECYCLE_ACTIVE,
        replacement_companion_id=None,
        expected_revision=expected_revision,
        companions=companions,
        owners=owners,
        lifecycle=lifecycle,
    )


async def _move(
    *,
    owner_id: str,
    companion_id: str,
    target: str,
    replacement_companion_id: str | None,
    expected_revision: int | None,
    companions: RosterReader,
    owners: OwnerReader,
    lifecycle: CompanionLifecycleWriter,
) -> CompanionLifecycleView:
    identity = await companions.get_owner_companion(owner_id, companion_id)
    route = companion_lifecycle_path(identity.lifecycle_state, target)
    if not route:
        # Already there. The retry a phone makes after a lost answer, and the
        # second tap on a button whose screen had not caught up — neither is a
        # conflict, and neither should write. The Owner pointer is read because
        # the answer names it either way; a shape that changed with
        # the route taken would make a client parse two contracts.
        owner = await owners.get_owner(owner_id)
        return CompanionLifecycleView(
            companion_id=identity.companion_id,
            lifecycle_state=identity.lifecycle_state,
            revision=identity.revision,
            default_companion_id=owner.default_companion_id,
        )

    # The revision travels from step to step, so every write is a compare-and-set
    # against the state the previous one produced rather than against a read that
    # is by then two moves old. A caller may pin the first step to what it saw on
    # screen; when it does not, the read above is the pin.
    revision: int | None = expected_revision or identity.revision
    result: CompanionLifecycleResult | None = None
    for step in route:
        result = await lifecycle.set_companion_lifecycle(
            owner_id,
            companion_id=companion_id,
            lifecycle_state=step,
            expected_revision=revision,
            # Only the step that hands the role over carries it. Sending it with
            # every step would rely on the authority ignoring it, and a caller
            # relying on being ignored is a caller that breaks when it stops
            # being ignored.
            replacement_companion_id=(
                replacement_companion_id if step == LIFECYCLE_RETIRING else None
            ),
        )
        revision = result.revision
    assert result is not None  # a non-empty route always produced one
    return CompanionLifecycleView(
        companion_id=result.companion_id,
        lifecycle_state=result.lifecycle_state,
        revision=result.revision,
        default_companion_id=result.default_companion_id,
    )
