"""Stand up an authenticated Controller session with a known Owner scope.

Owner scope used to arrive on the Bootstrap principal, so a test could put
``owner_id`` in a dict and be done. It does not any more: Bootstrap says who
the phone is, and the session resolves whose Workspace it is from the plane
that actually holds one — because a Host that recorded that answer itself went
on giving it after a data reset destroyed the Workspace, and stranded every
phone ever claimed onto it.

So a test that wants a session with an Owner has to say so where the resolving
happens. This says it in one place, and deliberately strips ``owner_id`` back
out of the principal it hands to the app: a test double that still offered it
would let a route quietly start reading it again, which is the whole defect.
"""

from __future__ import annotations

from typing import Any

from eidolon_admin_server.app.control_plane.contracts import WorkspaceOperation
from eidolon_admin_server.bootstrap.control import BootstrapControlClient
from eidolon_admin_server.local_api import app as local_api_app

#: Any well-formed Host id will do; the operation id is derived from it.
HOST_ID = "ehost-0123456789abcdefabcd"

_FINGERPRINT = "sha256:" + "0" * 64


def workspace_for(owner_id: str, operation_id: str) -> WorkspaceOperation:
    """The Data plane's answer for a Host that has been set up."""

    marker = operation_id.replace("-", "")
    return WorkspaceOperation.model_validate(
        {
            "contract_version": "1",
            "operation": "owner-workspace.initialize",
            "operation_id": operation_id,
            "request_fingerprint": _FINGERPRINT,
            "status": "succeeded",
            "owner": {
                "owner_id": owner_id,
                "display_name": "Owner",
                "lifecycle_state": "active",
            },
            "workspace": {
                "state": "ready",
                "primary_companion_id": f"c_{marker}",
                "persona_genome_id": f"g_{marker}_origin",
                "memory_realm_id": f"r_{marker}",
            },
        }
    )


def stub_controller_session(
    monkeypatch,
    principal: dict[str, Any],
    *,
    host_id: str = HOST_ID,
) -> None:
    """Authenticate this Controller, and answer what its Owner scope resolves to.

    ``principal`` is read for ``owner_id`` and then handed on without it, so
    the two halves of the fixture cannot describe a Host that could not exist:
    an Owner the Data plane has, and a Bootstrap that has never heard of one.
    ``owner_id=None`` is a Host nobody has set up — the Data plane answers that
    it has no Workspace, and Owner-scoped routes refuse with 409.
    """

    owner_id = principal.get("owner_id")
    handed_on = {key: value for key, value in principal.items() if key != "owner_id"}

    async def bootstrap_request(_self, operation: str, **_parameters):
        if operation in {"controller.authenticate", "controller.validate"}:
            return handed_on
        if operation == "descriptor":
            return {"contract_version": "1", "host_id": host_id}
        raise AssertionError(f"unexpected bootstrap operation: {operation}")

    async def resolved(_workspace, *, operation_id: str):
        return None if owner_id is None else workspace_for(owner_id, operation_id)

    monkeypatch.setattr(BootstrapControlClient, "request", bootstrap_request)
    # The seam the session resolves through. Patched here rather than through
    # the injected workspace client so these tests keep asserting that a
    # management route never reaches that client itself.
    monkeypatch.setattr(local_api_app, "existing_workspace", resolved)
