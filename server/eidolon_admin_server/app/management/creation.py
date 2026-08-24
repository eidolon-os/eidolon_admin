"""Adding an Eidolon: one authority write, and one signal that may be lost.

The write is the authority's. What lives here is the part that cannot live
there: a row in the Companion table is not a running memory process, and the
process that runs them is a different process on a different schedule (§II-6.4).

So this command does two things and treats them very differently:

- **provision**, which must happen exactly once and is therefore idempotent by
  the caller's operation id;
- **ask the memory supervisor to reconcile**, which is an accelerator. It is
  attempted only when a Realm was actually created, its failure is reported
  rather than raised, and nothing is rolled back when it fails.

That asymmetry is the whole design. The supervisor re-reads the roster on its
own, so an unheard signal costs time, not correctness. Treating it as part of
the write would mean a create that succeeded at the authority could be reported
as failed — and the retry would then find the Companion already there and have
to decide what that means.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from eidolon_admin_server.app.control_plane.contracts import CompanionProvision


@runtime_checkable
class CompanionProvisioner(Protocol):
    async def provision_companion(
        self,
        owner_id: str,
        *,
        operation_id: str,
        companion_display_name: str,
        kind: str,
    ) -> CompanionProvision: ...


@runtime_checkable
class MemoryReconciler(Protocol):
    async def request_reconcile(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class CreatedCompanion:
    """What a client is told after a create.

    ``memory_ready`` is deliberately three-valued in effect: ``True`` when
    nothing had to be brought up, ``True`` when the supervisor accepted the
    request, and ``False`` when a Realm was created and nobody could be told.
    ``False`` does not mean broken — it means "not yet", and a client should say
    so rather than either hiding it or presenting it as a failure.
    """

    companion_id: str
    display_name: str
    kind: str
    lifecycle_state: str
    revision: int
    #: True when this call created the Companion, False when it found it already
    #: created by the same operation. The Companion is the same either way.
    created: bool
    memory_ready: bool


async def create_companion(
    *,
    owner_id: str,
    operation_id: str,
    display_name: str,
    kind: str,
    companions: CompanionProvisioner,
    memory: MemoryReconciler | None,
) -> CreatedCompanion:
    provision = await companions.provision_companion(
        owner_id,
        operation_id=operation_id,
        companion_display_name=display_name,
        kind=kind,
    )

    memory_ready = True
    if provision.memory_realm_created:
        # Only here. Asking on every create would send a signal for a Realm
        # that has been running for months, and would make the common path
        # depend on a process the common path does not need.
        memory_ready = memory is not None and await memory.request_reconcile()

    return CreatedCompanion(
        companion_id=provision.companion.companion_id,
        display_name=provision.companion.display_name,
        kind=provision.companion.kind,
        lifecycle_state=provision.companion.lifecycle_state,
        revision=provision.companion.revision,
        created=not provision.replayed,
        memory_ready=memory_ready,
    )
