"""The public smart-home registry uses the authenticated Controller's Owner."""

from __future__ import annotations

import httpx
import pytest

from eidolon_admin_server.bootstrap.config import BootstrapMode, BootstrapSettings
from eidolon_admin_server.local_api.app import create_app
from eidolon_admin_server.local_api.config import LocalApiSettings
from tests.controller_session_support import stub_controller_session

pytestmark = pytest.mark.asyncio


class _Unused:
    def __getattr__(self, name):
        async def unused(*_args, **_kwargs):
            raise AssertionError(f"unexpected call: {name}")

        return unused


class _Backend:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def smarthome_registry(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        return {"schema_version": 1, "revision": len(self.calls), "areas": [],
                "devices": [], "scenes": [], "placements": []}


async def test_controller_owner_scope_and_revision_are_relayed(tmp_path, monkeypatch) -> None:
    controller_id = "ectrl-0123456789abcdefabcd"
    stub_controller_session(monkeypatch, {
        "contract_version": "1", "controller_id": controller_id,
        "role": "host_admin", "owner_id": "owner-1", "reset_epoch": 0,
    })
    backend = _Backend()
    unused = _Unused()
    app = create_app(
        LocalApiSettings(bootstrap=BootstrapSettings(
            mode=BootstrapMode.DEVELOPMENT,
            state_dir=tmp_path / "state", runtime_dir=tmp_path / "run",
            control_socket=tmp_path / "run/control.sock",
            ble_service_uuid="179e2e95-b1ee-5aa5-8dcf-7519b6c7ac52",
        )),
        workspace_client=unused, runtime_client=unused, devices_client=unused,
        device_admission_client=unused, host_services_client=unused,
        management_backend=backend,
    )
    path = "/api/management/v1/smarthome"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="https://local.test") as client:
        assert (await client.get(path + "/registry")).status_code == 401
        session = await client.post("/api/local/v1/auth/sessions", json={
            "contract_version": "1", "purpose": "eidolon-controller-local-auth-v1",
            "controller_id": controller_id,
            "challenge": "0123456789abcdefghijklmnopqrstuvwxyzABCDEFG",
            "reset_epoch": 0, "signature": "abcdefgh",
        })
        assert session.status_code == 200
        headers = {"Authorization": f"Bearer {session.json()['access_token']}"}
        read = await client.get(path + "/registry", headers=headers,
                                params={"owner_id": "someone-else"})
        write = await client.post(path + "/areas", headers=headers, json={
            "expected_revision": 1,
            "area": {"area_id": "living", "name": "客厅"},
        })
    assert read.status_code == 200
    assert write.status_code == 200
    assert [call["owner_id"] for call in backend.calls] == ["owner-1", "owner-1"]
    assert backend.calls[1]["payload"]["expected_revision"] == 1
