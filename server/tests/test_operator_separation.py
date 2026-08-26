"""Phase 6's two unverified exit conditions, written down as tests.

The plan's Phase 6 asks for three things. The third — operator surfaces live in
an Operator Shell — is satisfied by construction today (there is no Owner page
in the Admin Web at all). The other two were recorded as *not verified*: nobody
had written a test for either sentence, which is a different and worse state
than "not done", because the plan read as though they held.

They are testable now, and only partly by behaviour, so this file is explicit
about which half is which:

- **「普通 Controller 无法访问 operator API」** — the enforceable half is that the
  application a Controller can reach mounts no operator route, and that the
  operator application mounts nothing a Controller credential could open. Both
  are asserted below, and derived from the mounted apps rather than listed, so a
  route added tomorrow is covered by a test written today. The remaining half is
  **not a credential check**: operator routes carry no authentication, and what
  keeps them from the LAN is that the operator app binds loopback. That is worth
  stating plainly rather than dressing a 401 test around a check that does not
  exist — see the Phase 6 note in the plan.
- **「Mission Control 降级不影响任何管理 mutation」** — this one holds by
  construction and the construction is what gets asserted: Mission Control is
  read-only, and no management code path reaches it. A degraded read cannot
  affect a mutation that never consults it and that it cannot itself perform.
"""

from __future__ import annotations

import pathlib

import pytest

from eidolon_admin_server.bootstrap.config import BootstrapMode, BootstrapSettings
from eidolon_admin_server.local_api.app import create_app
from eidolon_admin_server.local_api.config import LocalApiSettings

#: The one plane the LAN-facing process serves on purpose (plan §3.4).
_OWNER_PLANES = ("/api/management/v1", "/api/local/v1", "/healthz")

_SERVER = pathlib.Path(__file__).resolve().parents[1] / "eidolon_admin_server"


class _Unused:
    def __getattr__(self, name):
        async def unused(*args, **kwargs):
            raise AssertionError(f"nothing here should be called: {name}")

        return unused


def _lan_app(tmp_path):
    unused = _Unused()
    return create_app(
        LocalApiSettings(
            bootstrap=BootstrapSettings(
                mode=BootstrapMode.DEVELOPMENT,
                state_dir=tmp_path / "state",
                runtime_dir=tmp_path / "run",
                control_socket=tmp_path / "run/control.sock",
                ble_service_uuid="179e2e95-b1ee-5aa5-8dcf-7519b6c7ac52",
            )
        ),
        workspace_client=unused,  # type: ignore[arg-type]
        runtime_client=unused,  # type: ignore[arg-type]
        devices_client=unused,  # type: ignore[arg-type]
        device_admission_client=unused,  # type: ignore[arg-type]
        host_services_client=unused,  # type: ignore[arg-type]
        management_backend=unused,
    )


def _paths(app) -> set[str]:
    found = set(app.openapi()["paths"])
    assert found, "no routes found; this gate would pass vacuously"
    return found


def test_the_surface_a_controller_reaches_mounts_no_operator_route(
    app, tmp_path
) -> None:
    """Derived, not listed.

    The operator set is *whatever the Admin app serves* minus the one internal
    ABI the LAN process is allowed to call. So a supervisor route, a config
    editor or a firmware tool added next month is in the forbidden set the day
    it is written, without anyone remembering to extend this test.
    """

    operator = {
        path
        for path in _paths(app)
        if not path.startswith("/api/internal/v1")
    }
    assert operator, "the Admin app served nothing but the internal ABI"

    reachable = _paths(_lan_app(tmp_path))
    assert reachable & operator == set()
    assert all(path.startswith(_OWNER_PLANES) for path in reachable), sorted(
        path for path in reachable if not path.startswith(_OWNER_PLANES)
    )


def test_the_operator_app_opens_nothing_with_a_controller_credential(app) -> None:
    """A Controller's token is minted and read in one process, and not this one.

    This is the structural half of "a Controller cannot reach operator API": not
    that the operator app refuses the credential, but that it has no route that
    would ever look at one. A sign-in route appearing here would mean a
    Controller could hold something this app understands — which is the moment
    the guarantee stops being about network placement and starts needing a real
    check.
    """

    assert not [path for path in _paths(app) if "/auth/" in path]

    seams = sorted(
        path.relative_to(_SERVER).as_posix()
        for path in (_SERVER / "app").rglob("*.py")
        if "controller.authenticate" in path.read_text(encoding="utf-8")
        or "authenticated_controller" in path.read_text(encoding="utf-8")
    )
    assert seams == []


def test_mission_control_cannot_change_anything(app) -> None:
    """Read-only, asserted rather than assumed.

    "Mission Control degradation does not affect any management mutation" is a
    sentence about coupling, and the strongest form of it is that the coupling
    cannot exist: a surface with no write route cannot be the thing that fails
    mid-mutation, however degraded its reads get.
    """

    writes = sorted(
        f"{method.upper()} {path}"
        for path, operations in app.openapi()["paths"].items()
        if path.startswith("/api/mission-control/")
        for method in operations
        if method.lower() not in {"get", "head", "options"}
    )
    assert writes == []


#: The Admin process's Mission Control composition — the thing whose
#: availability must never gate a management mutation. Matched precisely,
#: because the Owner's plane has a module of its own by the same name (the
#: devices join, which composes nothing and reaches no authority) and a check
#: that went by the word rather than the package would call that a dependency.
_ADMIN_COMPOSITION = (
    "from eidolon_admin_server.app.mission_control",
    "from ..mission_control",
    "from ...app.mission_control",
    "import eidolon_admin_server.app.mission_control",
)


def _imports_mission_control(path: pathlib.Path) -> bool:
    """Whether this module depends on the Admin composition at import time.

    An import is the dependency that matters. A loopback HTTP call to the
    internal plane is not one — that is how Local API reaches every capability,
    Mission Control included, and the LAN-facing process holds no operator code
    either way.
    """

    return any(
        line.startswith(_ADMIN_COMPOSITION)
        for line in (
            raw.strip() for raw in path.read_text(encoding="utf-8").splitlines()
        )
    )


#: The only management modules allowed to depend on Mission Control: the Owner's
#: projection and the one read that serves it. Both are read-only, which the
#: test below holds.
_OWNER_RUNTIME_READ = {
    "app/management/mission_control.py",
    "app/management/mission_control_router.py",
}


def test_no_management_mutation_depends_on_mission_control() -> None:
    """The other direction of the same sentence.

    Mission Control being read-only stops it corrupting a mutation; this stops it
    *blocking* one. A management handler that waited on the event stream — to
    publish, to confirm, to read a projection back — would make every mutation
    only as available as the weakest thing the operator dashboard watches.

    This used to be asserted as "no management file mentions mission_control at
    all", which was broader than the sentence it stood for and stopped being
    true the moment the Owner's own runtime map was exposed on their plane
    (2026-08-26). The Owner needs that read; no mutation may wait on it. So the
    boundary is now where it can be checked: the modules that perform mutations
    do not import Mission Control, and the two modules that do import it expose
    nothing but reads.
    """

    depending = sorted(
        path.relative_to(_SERVER).as_posix()
        for directory in ("app/management", "local_api")
        for path in (_SERVER / directory).rglob("*.py")
        if _imports_mission_control(path)
    )
    assert depending == sorted(_OWNER_RUNTIME_READ)


def test_the_owner_runtime_read_is_a_read() -> None:
    """The allowance above is only as safe as this.

    Two modules may depend on Mission Control. If either grew a mutation, the
    guarantee would be gone and the test above would still pass — so the shape
    of what they expose is asserted too, not just which files they are.
    """

    for relative in sorted(_OWNER_RUNTIME_READ):
        source = (_SERVER / relative).read_text(encoding="utf-8")
        for verb in ("@router.post", "@router.put", "@router.patch", "@router.delete"):
            assert verb not in source, f"{relative} performs mutations: {verb}"


def test_local_api_reaches_mission_control_the_way_it_reaches_everything() -> None:
    """Over the loopback plane, not by importing the composition.

    Keeps the LAN-facing process free of operator code: it knows a path and a
    credential, and nothing about how the reading is composed.
    """

    backend = (_SERVER / "local_api/management/backend.py").read_text(encoding="utf-8")
    assert "/api/internal/v1/management/mission-control/snapshot" in backend
    for relative in (
        "local_api/management/backend.py",
        "local_api/management/router.py",
        # The Owner plane's own join module: it takes a payload and an inventory
        # and returns a payload. It composes nothing and reaches no authority,
        # which is why it may share the name without sharing the dependency.
        "local_api/management/mission_control.py",
    ):
        assert not _imports_mission_control(_SERVER / relative), relative
