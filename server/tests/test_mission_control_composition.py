"""Mission Control against the service it actually gets.

This file exists because of a defect that was invisible in the ordinary way.
The cockpit reached for ``control_plane.data_authority``,
``.hub_management`` and ``.workspace_authority``. None of those exist on the
composed ``ControlPlaneService`` — it has ``.data``, ``.hub`` and
``.workspace``. Owner selection caught its own AttributeError and reported "no
Owner", so every request on a real Host answered with an empty snapshot, and
the router tests were green because they built their own control plane with the
other spelling.

That is now the fourth time in this line of work that a test double shaped
differently from the real thing hid a wiring bug. The pattern that catches it is
always the same: one test that uses the real composition.
"""

from __future__ import annotations

import httpx
import pytest

from eidolon_admin_server.app.control_plane.service import ControlPlaneService
from eidolon_admin_server.app.mission_control.service import (
    data_authority_of,
    hub_authority_of,
    workspace_authority_of,
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


async def test_the_owner_comes_from_something_that_can_answer_for_owners() -> None:
    service = _service()
    try:
        assert callable(workspace_authority_of(service).get_owner)
    finally:
        await service.close()


async def test_the_companion_runtime_comes_from_the_companion_authority() -> None:
    service = _service()
    try:
        assert callable(data_authority_of(service).get_owner_default_runtime)
    finally:
        await service.close()


async def test_devices_and_their_events_come_from_hub() -> None:
    service = _service()
    try:
        hub = hub_authority_of(service)
        assert callable(hub.list_devices)
        assert callable(hub.list_events)
    finally:
        await service.close()


async def test_an_empty_snapshot_is_not_how_a_wiring_bug_should_look() -> None:
    """The behaviour that hid the defect, pinned as the thing to notice.

    A missing attribute used to be reported as "no Owner" — indistinguishable
    from a Host nobody has claimed yet. The accessors above turn that class of
    mistake into an AttributeError at the one place it is spelled, and this test
    is what fails if someone re-inlines them.
    """
    from eidolon_admin_server.app.mission_control import service as mission_control
    from pathlib import Path

    source = Path(mission_control.__file__).read_text(encoding="utf-8")
    for wrong in ("control_plane.data_authority", "control_plane.hub_management",
                  "control_plane.workspace_authority"):
        # Allowed in prose, never in code: the comment above the accessors names
        # all three so the history stays findable.
        assert f"await {wrong}" not in source
        assert f"{wrong}." not in source.replace(f"``{wrong}``", "")
