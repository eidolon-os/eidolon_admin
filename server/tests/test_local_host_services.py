from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from eidolon_admin_server.bootstrap.config import BootstrapMode, BootstrapSettings
from eidolon_admin_server.bootstrap.control import BootstrapControlClient
from eidolon_admin_server.local_api.app import create_app
from eidolon_admin_server.local_api.config import LocalApiSettings
from eidolon_admin_server.local_api.host_services import host_vitals

pytestmark = pytest.mark.asyncio

_AUTH_CHALLENGE = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFG"
_CONTROLLER_ID = "ectrl-0123456789abcdefabcd"

_SERVICE = {
    "service_id": "eidolon-hub",
    "required": True,
    "enabled": True,
    "revision": 4,
    "runtime_state": "ready",
    "detail": None,
    "observed_at": "2026-08-11T00:00:00Z",
    "endpoints": [
        {
            "endpoint_id": "device-authority.http",
            "protocol": "http",
            "address": "http://127.0.0.1:8082",
            "contract": "hub/device-authority.v1",
        }
    ],
}


def _bootstrap(tmp_path: Path) -> BootstrapSettings:
    return BootstrapSettings(
        mode=BootstrapMode.DEVELOPMENT,
        state_dir=tmp_path / "state",
        runtime_dir=tmp_path / "run",
        control_socket=tmp_path / "run/control.sock",
        ble_service_uuid="179e2e95-b1ee-5aa5-8dcf-7519b6c7ac52",
    )


class _HostServicesPort:
    def __init__(self) -> None:
        self.mutations: list[tuple[str, str, int]] = []

    async def list_services(self) -> dict:
        return {"driver": "systemd", "services": [_SERVICE]}

    async def read_vitals(self) -> dict:
        return {
            "observed_at": "2026-08-25T09:00:00Z",
            "measurements": [
                {
                    "name": "disk.root",
                    "value": 31_200_000_000,
                    "capacity": 58_000_000_000,
                }
            ],
        }

    async def mutate(self, *, service_id, operation, expected_revision) -> dict:
        self.mutations.append((service_id, operation, expected_revision))
        return {
            "service_id": service_id,
            "operation": operation,
            "enabled": True,
            "revision": expected_revision + 1,
            "audit_position": 9,
            "replayed": False,
        }

    async def close(self) -> None:
        return None


class _UnusedPort:
    def __getattr__(self, name):
        raise AssertionError(f"unexpected client call: {name}")

    async def close(self) -> None:
        return None


def _app(tmp_path: Path, host_services) -> object:
    unused = _UnusedPort()
    return create_app(
        LocalApiSettings(bootstrap=_bootstrap(tmp_path)),
        workspace_client=unused,  # type: ignore[arg-type]
        runtime_client=unused,  # type: ignore[arg-type]
        devices_client=unused,  # type: ignore[arg-type]
        device_admission_client=unused,  # type: ignore[arg-type]
        host_services_client=host_services,  # type: ignore[arg-type]
    )


async def _authenticate(client: httpx.AsyncClient) -> dict[str, str]:
    session = await client.post(
        "/api/local/v1/auth/sessions",
        json={
            "contract_version": "1",
            "purpose": "eidolon-controller-local-auth-v1",
            "controller_id": _CONTROLLER_ID,
            "challenge": _AUTH_CHALLENGE,
            "reset_epoch": 0,
            "signature": "abcdefgh",
        },
    )
    return {"Authorization": f"Bearer {session.json()['access_token']}"}


async def test_a_phone_sees_and_restarts_the_same_services_admin_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    principal = {
        "contract_version": "1",
        "controller_id": _CONTROLLER_ID,
        "owner_id": "owner-1",
        "reset_epoch": 0,
    }

    async def bootstrap_request(self, operation: str, **_parameters):
        if operation in {"controller.authenticate", "controller.validate"}:
            return principal
        raise AssertionError(f"unexpected bootstrap operation: {operation}")

    monkeypatch.setattr(BootstrapControlClient, "request", bootstrap_request)
    port = _HostServicesPort()
    transport = httpx.ASGITransport(app=_app(tmp_path, port))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        anonymous = await client.get("/api/management/v1/host/services")
        headers = await _authenticate(client)
        listed = await client.get("/api/management/v1/host/services", headers=headers)
        restarted = await client.post(
            "/api/management/v1/host/services/eidolon-hub/restart",
            headers=headers,
            json={"expected_revision": 4},
        )
        stale = await client.post(
            "/api/management/v1/host/services/eidolon-hub/restart",
            headers=headers,
            json={},
        )

    assert anonymous.status_code == 401
    assert listed.status_code == 200
    services = listed.json()["services"]
    assert services[0]["service_id"] == "eidolon-hub"
    assert services[0]["revision"] == 4
    # Endpoint addresses are Admin's concern, not the Owner's.
    assert "endpoints" not in services[0]
    assert restarted.status_code == 200
    assert restarted.json()["revision"] == 5
    assert port.mutations == [("eidolon-hub", "restart", 4)]
    assert stale.status_code == 422


async def test_the_machine_is_readable_by_a_phone_that_holds_this_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deliberately not scoped to an Owner.

    A disk and a temperature are facts about the machine, not about whose
    Companion lives on it — and a Host whose Workspace was never set up still
    has a machine somebody may look at. The old route insisted on an Owner for
    the two service calls beside this one; that was an accident of where they
    were written, not a rule anyone chose.
    """

    principal = {
        "contract_version": "1",
        "controller_id": _CONTROLLER_ID,
        "reset_epoch": 0,
    }

    async def bootstrap_request(self, operation: str, **_parameters):
        if operation in {"controller.authenticate", "controller.validate"}:
            return principal
        raise AssertionError(f"unexpected bootstrap operation: {operation}")

    monkeypatch.setattr(BootstrapControlClient, "request", bootstrap_request)
    port = _HostServicesPort()
    transport = httpx.ASGITransport(app=_app(tmp_path, port))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        anonymous = await client.get("/api/management/v1/host/vitals")
        headers = await _authenticate(client)
        read = await client.get("/api/management/v1/host/vitals", headers=headers)

    assert anonymous.status_code == 401
    assert read.status_code == 200
    assert read.json()["operation"] == "host.vitals"
    assert read.json()["vitals"]


async def test_an_unknown_operation_is_refused_before_reaching_admin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    principal = {
        "contract_version": "1",
        "controller_id": _CONTROLLER_ID,
        "owner_id": "owner-1",
        "reset_epoch": 0,
    }

    async def bootstrap_request(self, operation: str, **_parameters):
        return principal

    monkeypatch.setattr(BootstrapControlClient, "request", bootstrap_request)
    port = _HostServicesPort()
    transport = httpx.ASGITransport(app=_app(tmp_path, port))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        response = await client.post(
            "/api/management/v1/host/services/eidolon-hub/uninstall",
            headers=headers,
            json={"expected_revision": 4},
        )

    assert response.status_code == 422
    assert port.mutations == []


def test_a_reading_the_host_could_not_take_is_not_reported_as_healthy() -> None:
    """The whole point of carrying the absence this far.

    A disk that could not be stat'ed is not an empty disk and not a fine one.
    It gets the words "读不到" and no concern at all, because not knowing is
    its own state — and the reason travels with it for whoever is diagnosing.
    """

    view = host_vitals(
        {
            "observed_at": "2026-08-18T00:00:00Z",
            "measurements": [
                {
                    "name": "disk.state",
                    "value": None,
                    "unavailable_reason": "/var/lib/eidolon: No such file or directory",
                }
            ],
        }
    )

    disk = view.vitals[0]
    assert disk.reading == "读不到"
    assert disk.concern == "none"
    assert "No such file" in (disk.unavailable_reason or "")


def test_the_judgement_is_made_here_and_reads_the_same_at_any_size() -> None:
    """What counts as "too little" is a product decision, so it lives here.

    Stated as fractions of capacity so a 32 GB card and a 2 TB disk are judged
    by the same rule rather than by a number that suits one of them.
    """

    def concern(free: float, total: float) -> str:
        view = host_vitals(
            {
                "observed_at": "2026-08-18T00:00:00Z",
                "measurements": [
                    {"name": "disk.state", "value": free, "capacity": total}
                ],
            }
        )
        return view.vitals[0].concern

    assert concern(30e9, 60e9) == "none"
    assert concern(9e9, 60e9) == "watch"
    assert concern(2e9, 60e9) == "act"
    # Same fractions, a card two orders of magnitude smaller.
    assert concern(300e6, 600e6) == "none"
    assert concern(30e6, 600e6) == "act"


def test_load_is_read_against_the_cores_it_is_spread_over() -> None:
    def concern(load: float, cores: int) -> str:
        view = host_vitals(
            {
                "observed_at": "2026-08-18T00:00:00Z",
                "measurements": [
                    {"name": "cpu.load1", "value": load, "capacity": cores}
                ],
            }
        )
        return view.vitals[0].concern

    # The same load number means opposite things on different machines, which
    # is why a percentage would have thrown the useful half away.
    assert concern(3.0, 4) == "none"
    assert concern(3.0, 1) == "act"


def test_readings_nobody_named_are_dropped_rather_than_shown_raw() -> None:
    view = host_vitals(
        {
            "observed_at": "2026-08-18T00:00:00Z",
            "measurements": [
                {"name": "some.future.counter", "value": 1.0},
                {"name": "temperature", "value": 48.6},
            ],
        }
    )

    # A Host that grows a new reading does not get to put its internal name on
    # someone's screen; it waits until this layer has words for it.
    assert [vital.name for vital in view.vitals] == ["温度"]
    assert view.vitals[0].reading == "48.6°C"
