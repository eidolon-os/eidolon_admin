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

from dataclasses import dataclass, replace
from typing import Protocol, runtime_checkable

from eidolon_sdk.biz.contracts.companion import LIFECYCLE_ACTIVE, LIFECYCLE_ARCHIVED

from eidolon_admin_server.app.control_plane.contracts import CompanionLifecycleResult


@runtime_checkable
class CompanionBodyRegistrar(Protocol):
    """The mounts this Owner has, and letting one go.

    Kernel's, not Data's: which device answers as which Eidolon is a fact about
    the Host's body mesh, and this layer only carries a decision to it.
    """

    async def list_owner_device_mounts(self, owner_id: str): ...

    async def release_device(
        self, *, owner_id: str, device_id: str, request_id: str, expected_revision: int
    ) -> None: ...


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
    #: Devices that answered as this Eidolon and no longer answer as anyone.
    #: Named so a screen can say so: a speaker that went quiet without a
    #: sentence is indistinguishable from a broken one.
    released_devices: tuple[str, ...] = ()


async def put_away(
    *,
    owner_id: str,
    companion_id: str,
    replacement_companion_id: str | None = None,
    expected_revision: int | None = None,
    lifecycle: CompanionLifecycleWriter,
    bodies: CompanionBodyRegistrar,
) -> CompanionLifecycleView:
    """Archive one of this Owner's Eidolons.

    ``replacement_companion_id`` is required only when this is the one that
    answers unaddressed requests, and the authority is what says so — by refusing
    with ``default_replacement_required``, which reaches the client intact so it
    can ask the person the question instead of guessing at the answer.
    """

    # Before the state moves, not after: a device attached to an archived
    # Companion is exactly the broken state this avoids, and nothing can attach
    # to one afterwards — Kernel refuses a Companion that is not active, so the
    # window closes itself rather than needing a second pass.
    released = await _release_bodies(
        owner_id=owner_id, companion_id=companion_id, bodies=bodies
    )
    view = _view(
        await lifecycle.set_companion_lifecycle(
            owner_id,
            companion_id=companion_id,
            lifecycle_state=LIFECYCLE_ARCHIVED,
            expected_revision=expected_revision,
            replacement_companion_id=replacement_companion_id,
        )
    )
    return replace(view, released_devices=released)


async def _release_bodies(
    *,
    owner_id: str,
    companion_id: str,
    bodies: CompanionBodyRegistrar,
) -> tuple[str, ...]:
    """Let go of every device that answers as this Eidolon.

    Each release carries the revision the mount was just read at, so two people
    archiving at once do not both write it, and a deterministic request id so a
    retry replays the same mutation instead of making a second one.

    A device that is no longer there between the read and the release is not an
    error — it is the same end state — so the refusal is left to the authority
    and this list says what was actually let go.
    """

    page = await bodies.list_owner_device_mounts(owner_id)
    released: list[str] = []
    for mount in page.mounts:
        if mount.attached_companion_id != companion_id or not mount.active:
            continue
        await bodies.release_device(
            owner_id=owner_id,
            device_id=mount.device_id,
            request_id=f"companion-archive-{companion_id}-{mount.device_id}",
            expected_revision=mount.revision,
        )
        released.append(mount.device_id)
    return tuple(released)


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
