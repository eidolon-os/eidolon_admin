"""What is mine, right now — the one read a client makes when it opens.

It exists because the alternative is four round trips and a screen that renders
in pieces, and because the composition needs facts from five places: that is the
kind of work a Host should do once rather than a phone doing badly.

What the tests hold is the honesty of a composed answer:

- **nothing missing is hidden.** A source that could not be read leaves its
  field empty *and* names itself, because an empty field with no explanation is
  indistinguishable from an empty truth;
- **one source failing does not fail the answer.** A Host that cannot reach its
  memory service still knows who its Owner is and who answers;
- **identifiers are not the answer.** A genome id means nothing to a person;
  「第 3 章 · 我发现你不喜欢被打断」 is the same fact in the form they can act on;
- **and the only part with no degraded form is the Owner**, because without it
  there is nothing for the rest to be about.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from eidolon_sdk.system.v1 import HostVitalsWire

from eidolon_admin_server.bootstrap.config import BootstrapMode, BootstrapSettings
from eidolon_admin_server.bootstrap.control import BootstrapControlClient
from eidolon_admin_server.local_api.app import create_app
from eidolon_admin_server.local_api.config import LocalApiSettings
from eidolon_admin_server.local_api.host_services import HostServiceControlError
from eidolon_admin_server.local_api.management.router import (
    ManagementBackendError,
    refusal_for_status,
)

pytestmark = pytest.mark.asyncio

_AUTH_CHALLENGE = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFG"
_CONTROLLER_ID = "ectrl-0123456789abcdefabcd"
_HOME = "/api/management/v1/home"


def _refused(message: str, status: int = 503) -> ManagementBackendError:
    return ManagementBackendError(
        message, status_code=status, refusal=refusal_for_status(status, message)
    )


class _Backend:
    def __init__(self, *, fails: set[str] = frozenset(), default: str | None = "c_01") -> None:
        self.fails = set(fails)
        self.default = default

    def _guard(self, name: str) -> None:
        if name in self.fails:
            raise _refused(f"{name} is away")

    async def context(self, *, owner_id: str) -> dict:
        self._guard("context")
        return {
            "owner_id": owner_id,
            "owner_display_name": "Manson",
            "owner_revision": 3,
            "default_companion_id": self.default,
            "capabilities": {},
            "limits": {},
        }

    async def roster(self, *, owner_id: str, cursor: str | None) -> dict:
        self._guard("roster")
        return {
            "companions": [
                {
                    "companion_id": "c_01",
                    "display_name": "小忆",
                    "kind": "conversational",
                    "lifecycle_state": "active",
                    "revision": 4,
                    "created_at": "2026-08-01T00:00:00+00:00",
                    "updated_at": "2026-08-01T00:00:00+00:00",
                },
                {
                    "companion_id": "c_02",
                    "display_name": "阿力",
                    "kind": "conversational",
                    "lifecycle_state": "archived",
                    "revision": 2,
                    "created_at": "2026-08-01T00:00:00+00:00",
                    "updated_at": "2026-08-01T00:00:00+00:00",
                },
            ],
            "next_cursor": None,
        }

    async def companion_face_state(self, *, owner_id: str, companion_id: str) -> dict:
        self._guard("face")
        return {"companion_id": companion_id, "has_face": True, "sha256": "abc"}

    async def persona_history(self, *, owner_id: str, companion_id: str) -> dict:
        self._guard("persona")
        return {
            "companion_id": companion_id,
            "chapters": [
                {
                    "chapter_id": "g_3",
                    "changed_at": "2026-08-20T00:00:00Z",
                    "what_changed": "我发现你不喜欢被打断",
                    "is_current": True,
                },
                {"chapter_id": "g_2", "changed_at": "x", "what_changed": "", "is_current": False},
                {"chapter_id": "g_1", "changed_at": "x", "what_changed": "", "is_current": False},
            ],
        }

    async def memory_library(self, *, owner_id: str, companion_id: str | None = None) -> dict:
        self._guard("memory")
        return {"wings": [], "entry_count": 42, "withheld_count": 2, "truncated": False}

    async def companion(self, *, owner_id: str, companion_id: str) -> dict:
        self._guard("companion")
        return {
            "companion_id": companion_id,
            "display_name": "小忆",
            "kind": "conversational",
            "lifecycle_state": "active",
            "revision": 4,
            "is_default": True,
        }

    def __getattr__(self, name):
        async def unused(*args, **kwargs):
            raise AssertionError(f"this test never calls {name}")

        return unused


def _mount(device_id: str, *, companion_id: str | None, state: str = "active"):
    return SimpleNamespace(
        device_id=device_id,
        claim=SimpleNamespace(
            device_ref=SimpleNamespace(
                device_instance_id=device_id,
                claim_generation=1,
                trust_epoch=1,
                owner_domain_generation=1,
            ),
            manifest_ref=SimpleNamespace(manifest_id="esp-box-3", revision=1),
            state=SimpleNamespace(value=state),
            updated_at=datetime(2026, 8, 20, 9, 0, tzinfo=UTC),
        ),
        mount=SimpleNamespace(attached_companion_id=companion_id, revision=2),
    )


class _Devices:
    def __init__(self, *devices, fails: bool = False) -> None:
        self.devices = list(devices)
        self.fails = fails

    async def list_devices(self, *, session):
        if self.fails:
            raise _refused("kernel is away")
        return SimpleNamespace(devices=tuple(self.devices))

    def __getattr__(self, name):
        async def unused(*args, **kwargs):
            raise AssertionError(f"this test never calls {name}")

        return unused


class _Host:
    def __init__(self, *vitals, fails: bool = False) -> None:
        self.vitals = list(vitals)
        self.fails = fails

    async def read_vitals(self) -> HostVitalsWire:
        if self.fails:
            raise HostServiceControlError("eidolond is away")
        return HostVitalsWire.model_validate({
            "operation": "system.host-vitals",
            "observed_at": "2026-08-25T09:00:00Z",
            "measurements": [
                {
                    "value": None,
                    "unit": "",
                    "capacity": None,
                    "unavailable_reason": None,
                    **measurement,
                }
                for measurement in self.vitals
            ],
        })

    async def list_services(self) -> dict:
        raise AssertionError("home never lists services")

    async def mutate(self, **kwargs):
        raise AssertionError("home never changes anything")


class _Unused:
    def __getattr__(self, name):
        async def unused(*args, **kwargs):
            raise AssertionError(f"nothing here should be called: {name}")

        return unused


def _app(tmp_path: Path, *, backend=None, devices=None, host=None):
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
        host_services_client=host or _Host(),  # type: ignore[arg-type]
        management_backend=backend or _Backend(),
        owner_device_port=devices or _Devices(),
    )


def _stub_controller(monkeypatch) -> None:
    principal = {
        "contract_version": "1",
        "controller_id": _CONTROLLER_ID,
        "role": "host_admin",
        "owner_id": "owner-1",
        "reset_epoch": 0,
    }

    async def bootstrap_request(self, operation: str, **_parameters):
        if operation in {"controller.authenticate", "controller.validate"}:
            return principal
        raise AssertionError(f"unexpected bootstrap operation: {operation}")

    monkeypatch.setattr(BootstrapControlClient, "request", bootstrap_request)


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


async def test_one_read_says_who_i_am_who_answers_and_what_is_waiting(
    tmp_path, monkeypatch
) -> None:
    _stub_controller(monkeypatch)
    app = _app(
        tmp_path,
        devices=_Devices(
            _mount("speaker-living-room", companion_id="c_01"),
            _mount("speaker-study", companion_id=None),
            _mount("old-box", companion_id="c_01", state="revoked"),
        ),
        host=_Host(
            {"name": "disk.root", "value": 1_900_000_000, "capacity": 55_900_000_000},
            {"name": "uptime", "value": 90_000},
        ),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        anonymous = await client.get(_HOME)
        headers = await _authenticate(client)
        home = (await client.get(_HOME, headers=headers)).json()

    assert anonymous.status_code == 401
    assert home["owner_display_name"] == "Manson"
    # Who answers, by name, with the chapter it is on said in words rather than
    # as a genome id.
    assert home["answering"]["display_name"] == "小忆"
    assert home["answering"]["persona_chapter"] == "第 3 章 · 我发现你不喜欢被打断"
    assert home["answering"]["memory"] == "记着 42 条，其中 2 条只给指定的伙伴"
    assert home["answering"]["has_face"] is True
    # The identifier is carried, not shown first.
    assert home["answering"]["persona_genome_id"] == "g_3"
    # Counts split the way a person acts on them: one Eidolon answering, one put
    # away; one device ready, one waiting to be pointed at somebody, one whose
    # access is gone.
    assert home["companions"] == {"total": 2, "ready": 1, "waiting": 0, "put_away": 1}
    assert home["devices"] == {"total": 3, "ready": 1, "waiting": 1, "put_away": 1}
    # The machine's own judgement, repeated verbatim, and only what it flagged.
    assert home["machine_attention"] == ["系统盘：1.8 GB 可用，共 52.1 GB"]
    assert home["unavailable"] == {}


async def test_a_source_that_could_not_be_read_names_itself(
    tmp_path, monkeypatch
) -> None:
    """An empty field with no explanation is indistinguishable from an empty
    truth, and a Host that cannot reach its memory still knows who answers."""

    _stub_controller(monkeypatch)
    app = _app(
        tmp_path,
        backend=_Backend(fails={"memory", "persona"}),
        devices=_Devices(fails=True),
        host=_Host(fails=True),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        home = (await client.get(_HOME, headers=headers)).json()

    assert home["owner_display_name"] == "Manson"
    assert home["answering"]["display_name"] == "小忆"
    assert home["answering"]["memory"] == ""
    assert home["answering"]["persona_chapter"] == ""
    assert home["devices"] == {"total": 0, "ready": 0, "waiting": 0, "put_away": 0}
    assert home["machine_attention"] == []
    # Every gap is named, so a client can say "the rest is true, and I could not
    # read those".
    assert set(home["unavailable"]) == {
        "answering_memory",
        "answering_persona",
        "devices",
        "machine",
    }


async def test_an_owner_who_has_named_nobody_is_a_state_not_a_gap(
    tmp_path, monkeypatch
) -> None:
    """Null is a real answer — every Eidolon put away, or none created — and no
    layer above may resolve it by picking one."""

    _stub_controller(monkeypatch)
    transport = httpx.ASGITransport(
        app=_app(tmp_path, backend=_Backend(default=None))
    )
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        home = (await client.get(_HOME, headers=headers)).json()

    assert home["answering"] is None
    assert home["unavailable"]["answering"] == "还没有指定由谁回答"
    # The rest of the answer still stands.
    assert home["companions"]["total"] == 2


async def test_without_an_owner_there_is_nothing_for_the_rest_to_be_about(
    tmp_path, monkeypatch
) -> None:
    """The one part with no degraded form, so it refuses rather than composing a
    home screen for nobody."""

    _stub_controller(monkeypatch)
    transport = httpx.ASGITransport(
        app=_app(tmp_path, backend=_Backend(fails={"context"}))
    )
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        refused = await client.get(_HOME, headers=headers)

    assert refused.status_code == 503


async def test_a_reading_the_host_could_not_take_is_not_silence(
    tmp_path, monkeypatch
) -> None:
    """Not knowing is not the same as being fine, and it is said in its own
    words rather than as an alarm."""

    _stub_controller(monkeypatch)
    transport = httpx.ASGITransport(
        app=_app(
            tmp_path,
            host=_Host(
                {"name": "memory.available", "unavailable_reason": "/proc/meminfo: absent"}
            ),
        )
    )
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        home = (await client.get(_HOME, headers=headers)).json()

    assert home["machine_attention"] == ["内存：读不到"]
