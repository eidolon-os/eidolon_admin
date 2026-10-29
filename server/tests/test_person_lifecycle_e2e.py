"""Public controller API → Admin → separate real Data process; injected erase outage."""

import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import httpx
import pytest
from fastapi import FastAPI
from eidolon_admin_server.app.control_plane.clients import (
    DataAuthorityClient,
    MemorySupervisorClient,
)
from eidolon_admin_server.app.control_plane.failure_handler import (
    install_authority_failure_handler,
)
from eidolon_admin_server.app.management.persons_router import router
from eidolon_admin_server.local_api.management.backend import AdminManagementClient
from tests.test_management_roster import _app, _authenticate, _stub_controller
from tests.test_memory_owner_surface_e2e import _free_port

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e]
TOKEN = "person-lifecycle-e2e-credential"
PID = "a" * 32


@pytest.fixture
def data_authority(tmp_path):
    data = Path(__file__).resolve().parents[3] / "eidolon_data"
    port = _free_port()
    with (tmp_path / "data.log").open("wb") as log:
        process = subprocess.Popen(
            [
                str(data / ".venv/bin/python"),
                str(data / "tests/harness/person_lifecycle_server.py"),
                "--port",
                str(port),
                "--state",
                str(tmp_path),
                "--token",
                TOKEN,
            ],
            cwd=data,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(150):
            if process.poll() is not None:
                pytest.fail((tmp_path / "data.log").read_text())
            try:
                response = httpx.get(
                    url + "/api/companion-authority/v1/owners/owner-1/persons",
                    headers={"Authorization": "Bearer " + TOKEN},
                    trust_env=False,
                )
                if response.status_code == 200:
                    break
            except httpx.TransportError:
                pass
            time.sleep(0.1)
        else:
            pytest.fail("Data authority did not start")
        yield url
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


async def test_archive_purge_failure_retry_and_restore_through_public_api(
    tmp_path, monkeypatch, data_authority
):
    _stub_controller(monkeypatch, owner_id="owner-1")
    calls = []

    def erase(request):
        calls.append(request)
        assert request.headers["Authorization"] == "Bearer " + TOKEN
        assert request.url.params["owner_id"] == "owner-1"
        assert request.url.params["person_id"] == PID
        return httpx.Response(
            503 if len(calls) == 1 else 200, json={"erased": len(calls) > 1}
        )

    async with (
        httpx.AsyncClient(trust_env=False) as dc,
        httpx.AsyncClient(transport=httpx.MockTransport(erase)) as mc,
    ):
        authority = DataAuthorityClient(
            directory=SimpleNamespace(
                resolve=AsyncMock(return_value=SimpleNamespace(address=data_authority))
            ),
            client=dc,
            service_token=TOKEN,
            timeout_seconds=5,
        )
        internal = FastAPI()
        internal.state.settings = SimpleNamespace(local_api_service_token=TOKEN)
        internal.state.control_plane = SimpleNamespace(
            data=authority,
            memory_supervisor=MemorySupervisorClient(
                base_url="http://memory",
                client=mc,
                timeout_seconds=5,
                service_token=TOKEN,
            ),
        )
        install_authority_failure_handler(internal)
        internal.include_router(router, prefix="/api")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=internal)) as ic:
            backend = AdminManagementClient(
                base_url="http://admin",
                client=ic,
                service_token=TOKEN,
                timeout_seconds=5,
            )
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=_app(tmp_path, backend)),
                base_url="http://local",
            ) as phone:
                base = "/api/management/v1/persons"
                assert (await phone.get(base)).status_code == 401
                phone.headers.update(await _authenticate(phone))
                created = await phone.post(
                    base, json={"person_id": PID, "display_name": "Test", "is_named": False}
                )
                assert created.status_code == 200, created.text
                assert created.json()["is_named"] is False
                assert created.json()["maturity"] == "established"
                rev = created.json()["revision"]
                memory_state = await phone.get(f"{base}/{PID}/memory-state")
                assert memory_state.status_code == 200, memory_state.text
                assert memory_state.json()["person_id"] == PID
                assert memory_state.json()["person_status"] == "active"
                assert memory_state.json()["state"] in ("active", "inactive", "unbound")

                assert (
                    await phone.post(
                        f"{base}/{PID}/permanent-delete",
                        params={"expected_revision": rev},
                    )
                ).status_code == 409
                assert not calls
                assert (
                    await phone.post(
                        f"{base}/{PID}/archive", params={"expected_revision": rev}
                    )
                ).status_code == 200
                archived = (await phone.get(base + "/archived")).json()["persons"][0]
                intent_revision = archived["revision"]
                failed = await phone.post(
                    f"{base}/{PID}/permanent-delete",
                    params={"expected_revision": intent_revision},
                )
                assert failed.status_code == 503, failed.text
                pending = (await phone.get(base + "/archived")).json()["persons"][0]
                assert (
                    await phone.post(
                        f"{base}/{PID}/restore",
                        params={"expected_revision": pending["revision"]},
                    )
                ).status_code == 409
                done = await phone.post(
                    f"{base}/{PID}/permanent-delete",
                    params={"expected_revision": pending["revision"]},
                )
                assert (
                    done.status_code == 200 and done.json()["state"] == "completed"
                ), done.text
                replay = await phone.post(
                    f"{base}/{PID}/permanent-delete",
                    params={"expected_revision": intent_revision},
                )
                assert replay.json() == done.json() and len(calls) == 2
                assert (await phone.get(base + "/archived")).json()["persons"] == []
                assert (await phone.get(f"{base}/{PID}")).status_code == 404
                assert (
                    await phone.post(
                        f"{base}/{PID}/restore",
                        params={"expected_revision": pending["revision"]},
                    )
                ).status_code == 404
                foreign = await dc.post(
                    data_authority
                    + "/api/companion-authority/v1/owners/foreign-owner/persons",
                    headers={"Authorization": "Bearer " + TOKEN},
                    json={"person_id": "b" * 32, "display_name": "Foreign"},
                )
                assert foreign.status_code == 200, foreign.text
                assert (await phone.get(base + "/" + "b" * 32)).status_code == 404
