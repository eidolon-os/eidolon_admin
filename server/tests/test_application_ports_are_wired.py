"""Every application port is satisfied by something the composed service has.

This is the gate for a failure that happened four times in one day, each time
the same way and each time invisible:

- Admin's consumed Companion identity drifted from Data's, and the producer test
  was pinned to an old commit so nothing went red;
- ``/context`` asked the Companion authority for the Owner aggregate, which it
  cannot answer, and every test injected its own reader;
- the create path's ports were only ever exercised with stubs;
- Mission Control reached for three attributes that do not exist on the composed
  service, and its router tests built a control plane with the other spelling.

In all four, unit tests passed against a double shaped differently from the real
thing. Writing "add a composition test for every cross-process read" in a plan
does not prevent the fifth one; **discovering** the ports does.

So this file finds every ``Protocol`` in the application layer and requires each
one to be declared here with the attribute of the composed service that
satisfies it. A new port with no entry fails — not because someone remembered to
add a test, but because the test looks for it.

What this does not check is behaviour: whether the call returns the right thing
is what the unit tests with their stubs are for, and they are right to use them.
This checks only the thing a stub structurally cannot — that the object the
route reaches for can answer at all.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import httpx
import pytest

from eidolon_admin_server.app.control_plane.service import ControlPlaneService
from eidolon_admin_server.app.management.context import OwnerReader
from eidolon_admin_server.app.management.creation import (
    CompanionProvisioner,
    MemoryReconciler,
)
from eidolon_admin_server.app.management.audience import MemoryAudienceKeeper
from eidolon_admin_server.app.management.forgetting import MemoryForgetter
from eidolon_admin_server.app.management.memory import MemoryBrowser
from eidolon_admin_server.app.management.activity import CompanionActivityReader
from eidolon_admin_server.app.management.persona import PersonaHistorian
from eidolon_admin_server.app.management.recollecting import MemoryRecollector
from eidolon_admin_server.app.management.sessions import RuntimeSessionRevoker
from eidolon_admin_server.app.management.roster import (
    DefaultCompanionWriter,
    RosterReader,
)
from eidolon_admin_server.app.settings import Settings

pytestmark = pytest.mark.asyncio

APP = Path(__file__).resolve().parents[1] / "eidolon_admin_server/app"

#: Every application-layer Protocol, and the attribute of the composed
#: ``ControlPlaneService`` that must satisfy it. ``None`` means the port is
#: deliberately not served by that service — say why, next to the entry.
PORTS: dict[str, tuple[object | None, str | None]] = {
    "OwnerReader": (OwnerReader, "workspace"),
    "RosterReader": (RosterReader, "data"),
    "DefaultCompanionWriter": (DefaultCompanionWriter, "workspace"),
    "CompanionProvisioner": (CompanionProvisioner, "workspace"),
    "MemoryReconciler": (MemoryReconciler, "memory_supervisor"),
    # Reading what a memory holds and bringing a realm *up* are different
    # things on different clients; the gate below asserts they are not
    # interchangeable.
    "MemoryBrowser": (MemoryBrowser, "memory"),
    "MemoryForgetter": (MemoryForgetter, "memory"),
    "MemoryAudienceKeeper": (MemoryAudienceKeeper, "memory"),
    "MemoryRecollector": (MemoryRecollector, "memory"),
    # Persona history is Data's, like the roster: same authority, and the gate
    # below asserts the two clients are not interchangeable.
    "PersonaHistorian": (PersonaHistorian, "data"),
    # Conversations and long tasks are the Agent's, including their state
    # machine: this port reads them and relays two actions.
    "CompanionActivityReader": (CompanionActivityReader, "activity"),
    # Same client as the reads above: the runtime that holds the sessions is the
    # one that ends them.
    "RuntimeSessionRevoker": (RuntimeSessionRevoker, "activity"),
    # Admin's own store, constructed by the service rather than reached for on
    # it: there is no authority behind it and nothing to mis-wire.
    "RemovalIntentStore": (None, None),
    "AdmissionDecisionIntentStore": (None, None),
}


def _declared_protocols() -> set[str]:
    """Protocol classes in the application layer, read from the source.

    From the source rather than by importing every module, so a port in a module
    that nothing imports yet still counts. That module is exactly where a
    forgotten wiring would sit.
    """

    found: set[str] = set()
    for path in sorted(APP.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            if any(
                isinstance(base, ast.Name) and base.id == "Protocol"
                for base in node.bases
            ):
                found.add(node.name)
    return found


def _service() -> ControlPlaneService:
    """The real composition. Nothing is called, so no network is needed."""
    return ControlPlaneService.build(
        settings=Settings(
            data_authority_token="data-token",
            data_workspace_authority_token="workspace-token",
        ),
        http_client=httpx.AsyncClient(),
    )


async def test_every_application_port_is_accounted_for() -> None:
    """A new Protocol has to be declared above, with what satisfies it.

    This is the half that cannot be forgotten. The alternative — remembering to
    write a composition test — is what was tried implicitly four times.
    """
    declared = _declared_protocols()
    missing = declared - set(PORTS)
    stale = set(PORTS) - declared

    assert not missing, (
        f"new application port(s) with no wiring declared: {sorted(missing)}. "
        "Add each to PORTS with the ControlPlaneService attribute that satisfies "
        "it, or None plus a reason if nothing on that service serves it."
    )
    assert not stale, f"PORTS names protocols that no longer exist: {sorted(stale)}"


async def test_each_declared_port_is_satisfied_by_what_it_names() -> None:
    service = _service()
    try:
        for name, (protocol, attribute) in PORTS.items():
            if protocol is None or attribute is None:
                continue
            assert hasattr(service, attribute), (
                f"{name} names ControlPlaneService.{attribute}, which does not "
                "exist — the shape of the Mission Control defect"
            )
            assert isinstance(getattr(service, attribute), protocol), (
                f"ControlPlaneService.{attribute} does not satisfy {name}"
            )
    finally:
        await service.close()


async def test_the_protocols_are_checkable_at_runtime() -> None:
    """Otherwise the assertion above cannot be made and would be skipped.

    A ``Protocol`` without ``@runtime_checkable`` raises on ``isinstance``, and
    the tempting fix is to drop the check for that one port — which is how a gate
    stops covering the thing it exists for.
    """
    for name, (protocol, attribute) in PORTS.items():
        if protocol is None or attribute is None:
            continue
        assert getattr(protocol, "_is_runtime_protocol", False), (
            f"{name} is not @runtime_checkable, so its wiring cannot be checked"
        )


async def test_a_port_is_reached_for_by_name_not_by_luck() -> None:
    """The two authorities must not be interchangeable.

    If ``.data`` satisfied the Owner protocols too, the Mission Control defect
    would have been harmless and this gate would prove nothing. It is not
    harmless, so this asserts the distinction the gate depends on.
    """
    service = _service()
    try:
        assert not isinstance(service.data, OwnerReader)
        assert not isinstance(service.data, DefaultCompanionWriter)
        assert not isinstance(service.workspace, RosterReader)
        # And the memory *read* client cannot bring a realm up, which is the
        # confusion the create path would make if it reached for "memory".
        assert not isinstance(service.memory, MemoryReconciler)
        assert not isinstance(service.memory_supervisor, MemoryBrowser)
    finally:
        await service.close()


async def test_the_gate_reads_the_layer_it_claims_to() -> None:
    """Otherwise it passes by finding no protocols at all."""
    assert APP.is_dir()
    assert len(_declared_protocols()) >= len(PORTS) - 1
    assert inspect.isclass(OwnerReader)
