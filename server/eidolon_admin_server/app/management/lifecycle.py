"""Putting an Eidolon away, and bringing it back.

Thin, and it took a wrong turn to get here. The first version walked the
lifecycle graph — `active` → `retiring` → `archived` — issuing one authority
call per step, on the reasoning that the order is a safety property and the
projection should honour it. Two things were wrong with that.

**The order is the authority's to keep, and it kept it either way.** A caller
that walks a state machine over HTTP is a coordinator without a coordinator's
guarantees: two round trips, and a window in between where a Host that dies
leaves a Companion `retiring` — not where they were, not where they asked to be,
and a state no screen offered a way out of. Nothing runs between those steps
today (no drain primitive, no BodyAssignment), so the walk bought a failure mode
and no safety. Data now reaches `archived` in one transaction, and the granular
commands are still there for the workflow that will one day have work to do in
between.

**Ownership was proved twice.** This layer read the Companion through the
owner-scoped route first, the way the persona projection has to — but the
lifecycle route takes the Owner itself and answers 404 for someone else's. A
second check against the same fact is not caution; it is a second place to be
wrong.

What is left is what a projection is for: a name for the product action, a shape
the boundary can return, and refusals relayed with the authority's own code —
including the one that is really a question, so a client can ask a person "who
should answer instead?" rather than showing them a conflict.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from eidolon_sdk.biz.contracts.companion import LIFECYCLE_ACTIVE, LIFECYCLE_ARCHIVED

from eidolon_admin_server.app.control_plane.contracts import CompanionLifecycleResult


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
    the same transaction. A client that had to ask again would be showing a
    person "archived" beside a stale answer to "so who talks to me now".
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
    lifecycle: CompanionLifecycleWriter,
) -> CompanionLifecycleView:
    """Archive one of this Owner's Eidolons.

    ``replacement_companion_id`` is required only when this is the one that
    answers unaddressed requests, and the authority is what says so — by refusing
    with ``default_replacement_required``, which reaches the client intact so it
    can ask the person the question instead of guessing at the answer.
    """

    return _view(
        await lifecycle.set_companion_lifecycle(
            owner_id,
            companion_id=companion_id,
            lifecycle_state=LIFECYCLE_ARCHIVED,
            expected_revision=expected_revision,
            replacement_companion_id=replacement_companion_id,
        )
    )


async def bring_back(
    *,
    owner_id: str,
    companion_id: str,
    expected_revision: int | None = None,
    lifecycle: CompanionLifecycleWriter,
) -> CompanionLifecycleView:
    """Make an archived Eidolon answer again.

    It does not take the default role back. Restoring says "this one is here
    again"; who answers when nobody is named is a separate thing the Owner said,
    and quietly reversing it would undo a decision they made.
    """

    return _view(
        await lifecycle.set_companion_lifecycle(
            owner_id,
            companion_id=companion_id,
            lifecycle_state=LIFECYCLE_ACTIVE,
            expected_revision=expected_revision,
        )
    )


def _view(result: CompanionLifecycleResult) -> CompanionLifecycleView:
    return CompanionLifecycleView(
        companion_id=result.companion_id,
        lifecycle_state=result.lifecycle_state,
        revision=result.revision,
        default_companion_id=result.default_companion_id,
    )
