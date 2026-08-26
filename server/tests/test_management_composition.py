"""The wiring itself, asserted — not just the layers on either side of it.

Every other management test injects its own reader, which is what unit tests
should do and is also how a real defect survived: the ``/context`` route reached
for ``control_plane.data`` for the Owner aggregate, which lives on
``control_plane.workspace``. The composed object had no ``get_owner`` at all, so
the route raised AttributeError on a Host while every test stayed green.

So this file asserts the one thing stubs cannot: that the objects the routes
actually reach for satisfy the Protocols those routes require.
"""

from __future__ import annotations

import httpx
import pytest

from eidolon_admin_server.app.control_plane.service import ControlPlaneService
from eidolon_admin_server.app.management.context import OwnerReader
from eidolon_admin_server.app.management.creation import (
    CompanionProvisioner,
    MemoryReconciler,
)
from eidolon_admin_server.app.management.roster import (
    DefaultCompanionWriter,
    RosterReader,
    RuntimeReader,
)
from eidolon_admin_server.app.settings import Settings

pytestmark = pytest.mark.asyncio


def _service() -> ControlPlaneService:
    """The real composition. Nothing is called, so no network is needed."""
    return ControlPlaneService.build(
        settings=Settings(
            data_authority_token="data-token",
            data_workspace_authority_token="workspace-token",
        ),
        http_client=httpx.AsyncClient(),
    )


async def test_the_owner_reader_the_routes_reach_for_can_read_an_owner() -> None:
    service = _service()
    try:
        assert isinstance(service.workspace, OwnerReader)
    finally:
        await service.close()


async def test_the_roster_reader_the_routes_reach_for_can_read_companions() -> None:
    service = _service()
    try:
        assert isinstance(service.data, RosterReader)
    finally:
        await service.close()


async def test_the_default_writer_the_route_reaches_for_can_write_it() -> None:
    """The pointer is on the Owner, so its writer is the workspace authority."""
    service = _service()
    try:
        assert isinstance(service.workspace, DefaultCompanionWriter)
    finally:
        await service.close()


async def test_the_provisioner_the_route_reaches_for_can_provision() -> None:
    """A Companion is created through the workspace authority's write path."""
    service = _service()
    try:
        assert isinstance(service.workspace, CompanionProvisioner)
    finally:
        await service.close()


async def test_the_reconciler_the_route_reaches_for_can_be_asked() -> None:
    """And it is a different thing from the memory *read* client.

    Both are "memory" to a reader skimming the composition; only one of them can
    bring a Realm up, and the create path reaches for that one.
    """
    service = _service()
    try:
        assert isinstance(service.memory_supervisor, MemoryReconciler)
        assert not isinstance(service.memory, MemoryReconciler)
    finally:
        await service.close()


async def test_the_two_authorities_are_not_interchangeable() -> None:
    """Which is the point. If they were, the defect above would not have been one.

    Asserted so that "just use .data for everything" fails here rather than at
    the first request on a Host.
    """
    service = _service()
    try:
        assert not isinstance(service.data, OwnerReader)
        assert not isinstance(service.workspace, RosterReader)
        assert not isinstance(service.data, DefaultCompanionWriter)
    finally:
        await service.close()


async def test_the_runtime_reader_the_roster_reaches_for_can_say_what_is_live() -> None:
    """Which Companions are running is the Agent's fact, not the authority's.

    Data knows what exists and what state its life is in; only the process
    holding the runtimes knows which ones it is holding. Asking the wrong one
    is how a screen ends up inferring "running" from "is the default".
    """
    service = _service()
    try:
        assert isinstance(service.activity, RuntimeReader)
        assert not isinstance(service.data, RuntimeReader)
    finally:
        await service.close()
