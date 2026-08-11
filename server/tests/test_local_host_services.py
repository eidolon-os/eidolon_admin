from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from eidolon_admin_server.bootstrap.config import BootstrapMode, BootstrapSettings
from eidolon_admin_server.bootstrap.control import BootstrapControlClient
from eidolon_admin_server.local_api.app import create_app
from eidolon_admin_server.local_api.config import LocalApiSettings

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


async def test_mobile_sees_and_restarts_the_same_services_admin_does(
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
        anonymous = await client.get("/api/local/v1/host/services")
        headers = await _authenticate(client)
        listed = await client.get("/api/local/v1/host/services", headers=headers)
        restarted = await client.post(
            "/api/local/v1/host/services/eidolon-hub/restart",
            headers=headers,
            json={"expected_revision": 4},
        )
        stale = await client.post(
            "/api/local/v1/host/services/eidolon-hub/restart",
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
            "/api/local/v1/host/services/eidolon-hub/uninstall",
            headers=headers,
            json={"expected_revision": 4},
        )

    assert response.status_code == 422
    assert port.mutations == []
