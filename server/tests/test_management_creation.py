"""Creating an Eidolon: one write that must happen once, one signal that may fail.

The asymmetry between those two is the design, and it is the kind of design a
later reader "fixes" by making the signal mandatory. So it is pinned here:

- the Realm reconcile request is attempted only when a Realm was created;
- its failure does not fail the create, and nothing is rolled back;
- what a client is told instead is ``memory_ready: false``, which means "not
  yet", not "broken".

The reason is that the memory supervisor re-reads the authority roster on its
own schedule. An unheard signal costs time, not correctness. Making it fatal
would mean a create that *succeeded* at the authority gets reported as failed —
and the retry then finds the Companion already there and has to decide what that
means.
"""

from __future__ import annotations

import pytest

from eidolon_sdk.biz.persona import PersonaAuthoring

from eidolon_admin_server.app.control_plane.contracts import (
    CompanionProvision,
    ProvisionedCompanion,
)
from eidolon_admin_server.app.management.creation import create_companion

pytestmark = pytest.mark.asyncio

_OPERATION = "32c421a3-e0df-40f9-8f75-68745ae39d81"


def _provision(*, realm_created: bool, replayed: bool = False) -> CompanionProvision:
    return CompanionProvision(
        contract_version="1",
        operation="companion.provision",
        operation_id=_OPERATION,
        request_fingerprint="sha256:" + "0" * 64,
        companion=ProvisionedCompanion(
            companion_id="cp-1",
            display_name="阿力",
            kind="conversational",
            lifecycle_state="active",
            revision=1,
        ),
        persona_genome_id="gp-1",
        memory_realm_id="r-1",
        memory_realm_created=realm_created,
        replayed=replayed,
    )


class _Authority:
    def __init__(self, provision: CompanionProvision) -> None:
        self.provision = provision
        self.calls: list[tuple[str, str, str, str]] = []
        self.personas: list[object] = []

    async def provision_companion(
        self, owner_id, *, operation_id, companion_display_name, kind, persona=None
    ):
        self.calls.append((owner_id, operation_id, companion_display_name, kind))
        self.personas.append(persona)
        return self.provision


class _Supervisor:
    def __init__(self, *, accepts: bool) -> None:
        self.accepts = accepts
        self.asked = 0

    async def request_reconcile(self) -> bool:
        self.asked += 1
        return self.accepts


async def _create(authority, supervisor):
    return await create_companion(
        owner_id="owner-1",
        operation_id=_OPERATION,
        display_name="阿力",
        kind="conversational",
        companions=authority,
        memory=supervisor,
    )


async def test_a_shared_realm_is_not_announced_to_anyone() -> None:
    """The common case: the Owner's memory has been running for months.

    Asking for a reconcile here would make the ordinary create depend on a
    process the ordinary create does not need.
    """
    supervisor = _Supervisor(accepts=True)
    created = await _create(_Authority(_provision(realm_created=False)), supervisor)

    assert supervisor.asked == 0
    assert created.memory_ready is True


async def test_a_new_realm_is_announced_once() -> None:
    supervisor = _Supervisor(accepts=True)
    created = await _create(_Authority(_provision(realm_created=True)), supervisor)

    assert supervisor.asked == 1
    assert created.memory_ready is True


async def test_a_supervisor_that_cannot_be_told_does_not_fail_the_create() -> None:
    """The Companion exists. Saying otherwise would be false.

    A create reported as failed invites a retry, the retry finds the Companion
    already there, and now the client has to decide whether that was its own
    doing. All of that to avoid saying "its memory is still coming up".
    """
    supervisor = _Supervisor(accepts=False)
    created = await _create(_Authority(_provision(realm_created=True)), supervisor)

    assert created.companion_id == "cp-1"
    assert created.created is True
    assert created.memory_ready is False


async def test_a_host_with_no_supervisor_configured_still_creates() -> None:
    """A Host that cannot be told is the same case as one that would not answer."""
    created = await create_companion(
        owner_id="owner-1",
        operation_id=_OPERATION,
        display_name="阿力",
        kind="conversational",
        companions=_Authority(_provision(realm_created=True)),
        memory=None,
    )

    assert created.created is True
    assert created.memory_ready is False


async def test_a_replay_announces_nothing() -> None:
    """The original caller owned that signal.

    A replay created no Realm, so there is nothing to bring up; sending a signal
    per retry would turn a lossy network into repeated work on the Host.
    """
    supervisor = _Supervisor(accepts=True)
    created = await _create(
        _Authority(_provision(realm_created=False, replayed=True)), supervisor
    )

    assert supervisor.asked == 0
    assert created.created is False
    assert created.memory_ready is True


async def test_the_operation_id_reaches_the_authority_unchanged() -> None:
    """It is the caller's, and it is what makes the retry idempotent."""
    authority = _Authority(_provision(realm_created=False))
    await _create(authority, _Supervisor(accepts=True))

    assert authority.calls == [
        ("owner-1", _OPERATION, "阿力", "conversational")
    ]


async def test_the_authoring_reaches_the_authority_unchanged() -> None:
    """Relayed, not interpreted.

    Who an Eidolon starts out as is a genome, and genomes belong to the persona
    authority. If this layer touched the authoring — filled a blank, normalised
    a list, chose a default — it would become a second place that decides what
    an Eidolon is, and the two would eventually disagree.
    """

    authored = PersonaAuthoring(self_concept="我记得你说过的话", values=["诚实"])
    authority = _Authority(_provision(realm_created=False))

    await create_companion(
        owner_id="owner-1",
        operation_id=_OPERATION,
        display_name="小南",
        kind="conversational",
        persona=authored,
        companions=authority,
        memory=None,
    )

    assert authority.personas == [authored]


async def test_asking_for_nothing_says_nothing_about_the_persona() -> None:
    """``None`` travels as ``None``.

    A layer that turned "nobody authored anything" into an empty draft would be
    stating a personality on the person's behalf — and an empty draft is not the
    same request as no draft, which matters because the authority fingerprints
    what it receives.
    """

    authority = _Authority(_provision(realm_created=False))

    await create_companion(
        owner_id="owner-1",
        operation_id=_OPERATION,
        display_name="小南",
        kind="conversational",
        companions=authority,
        memory=None,
    )

    assert authority.personas == [None]
