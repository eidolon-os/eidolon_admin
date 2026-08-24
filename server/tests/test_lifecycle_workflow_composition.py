"""The removal daemon must be able to build the service it serves with.

This is the one composition that no request exercises: the daemon builds its
ControlPlaneService at process start, before it accepts anything. A missing
collaborator there is not a failing request — it is a systemd unit that will not
start, and with it every device removal on the Host. That is exactly how
`activity` reached a Pi: the service grew a required collaborator, every route
test kept passing, and the unit crashed on boot with a TypeError.
"""

from __future__ import annotations

import getpass
import inspect
from pathlib import Path

import pytest

from eidolon_admin_server.app.control_plane.service import ControlPlaneService
from eidolon_admin_server.lifecycle_workflow.daemon import _build_service
from eidolon_admin_server.lifecycle_workflow.settings import (
    load_lifecycle_workflow_settings,
)


def _settings(tmp_path: Path):
    return load_lifecycle_workflow_settings(
        {
            "EIDOLON_LIFECYCLE_WORKFLOW_SOCKET": str(tmp_path / "workflow.sock"),
            "EIDOLON_LIFECYCLE_STATE_DIR": str(tmp_path),
            "EIDOLON_LIFECYCLE_REMOVAL_CAPABILITY_SOCKET": str(
                tmp_path / "broker.sock"
            ),
            "EIDOLON_LIFECYCLE_SYSTEM_DIRECTORY_UDS": str(tmp_path / "system.sock"),
            # A Host runs this as its own account; this suite does not have one.
            "EIDOLON_LIFECYCLE_ALLOWED_LOCAL_API_USER": getpass.getuser(),
        }
    )


@pytest.mark.asyncio
async def test_the_removal_daemon_builds_its_service(tmp_path: Path) -> None:
    service = _build_service(_settings(tmp_path))
    try:
        # Every collaborator the service declares is supplied — the point of the
        # test. Reading them by name rather than asserting a count keeps this
        # honest when the service grows another one.
        for name in (
            "directory",
            "data",
            "workspace",
            "hub",
            "kernel",
            "memory",
            "activity",
        ):
            assert getattr(service, name) is not None
        assert service.removal_intents is not None
        assert service.hub_credentials is not None
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_an_authority_this_workflow_does_not_reach_says_which(
    tmp_path: Path,
) -> None:
    service = _build_service(_settings(tmp_path))
    try:
        with pytest.raises(AttributeError, match="does not reach Data"):
            service.data.fetch_owner
        with pytest.raises(AttributeError, match="does not reach Agent activity"):
            service.activity.list_conversations
    finally:
        await service.close()


def test_every_required_collaborator_is_named_at_this_composition_site() -> None:
    """A new required collaborator must be a decision, not a boot-time surprise.

    The daemon passes each one explicitly, so this compares the two lists and
    fails in the suite rather than on a Host.
    """

    required = {
        name
        for name, parameter in inspect.signature(
            ControlPlaneService.__init__
        ).parameters.items()
        if parameter.kind is inspect.Parameter.KEYWORD_ONLY
        and parameter.default is inspect.Parameter.empty
    }
    source = inspect.getsource(_build_service)
    missing = {name for name in required if f"{name}=" not in source}
    assert not missing, f"the removal daemon does not name: {sorted(missing)}"
