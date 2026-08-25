"""Which phones may manage this Host.

The one part of the management surface the LAN-facing process serves itself.
That is deliberate and worth pinning: the backend boundary exists so this
process holds no *authority* credential, and a controller grant is not an
authority's data — it is this Host's own trust root, which this process must
already reach because it is what every request here is authenticated against.

What the tests hold:

- the list is answered to a phone that already holds this Host, and says which
  row is the phone asking;
- a public key is not part of what a person is shown;
- a Host that cannot answer says so rather than showing an empty list, because
  "no phones may manage this Host" is a sentence that should never be invented.
"""

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
_OTHER_ID = "ectrl-ffffffffffffffffffff"
_CONTROLLERS = "/api/management/v1/controllers"


def _grant(controller_id: str, *, name: str) -> dict:
    return {
        "controller_id": controller_id,
        "public_key": "MCowBQYDK2VwAyEA" + "a" * 28,
        "public_key_fingerprint": "sha256:ab12",
        "role": "host_admin",
        "display_name": name,
        "platform": "android",
        "reset_epoch": 0,
        "created_at": "2026-08-20T09:00:00Z",
        "revoked_at": None,
    }


class _Bootstrap:
    """The Host's control socket, answering the three controller operations."""

    def __init__(self) -> None:
        self.asked: list[tuple[str, dict]] = []
        self.grants = [
            _grant(_CONTROLLER_ID, name="书房的手机"),
            _grant(_OTHER_ID, name="客厅的平板"),
        ]

    async def list_controllers(self, controller_id: str) -> dict:
        self.asked.append(("list", {"controller_id": controller_id}))
        return {"controllers": self.grants}

    async def invite_controller(self, controller_id: str, ttl_seconds: int | None) -> dict:
        self.asked.append(
            ("invite", {"controller_id": controller_id, "ttl_seconds": ttl_seconds})
        )
        return {
            "setup_code": "1234-5678",
            "expires_at": "2026-08-25T10:00:00Z",
            "session_id": "not-for-a-person",
        }

    async def revoke_controller(self, controller_id: str, target_id: str) -> dict:
        self.asked.append(
            ("revoke", {"controller_id": controller_id, "target_id": target_id})
        )
        return {"controller": _grant(target_id, name="客厅的平板")}


class _Unused:
    def __getattr__(self, name):
        async def unused(*args, **kwargs):
            raise AssertionError(f"nothing here should be called: {name}")

        return unused


def _app(tmp_path: Path, bootstrap):
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
        controller_directory=bootstrap,
    )


def _stub_controller(monkeypatch, *, owner_id: str | None = "owner-1") -> None:
    principal = {
        "contract_version": "1",
        "controller_id": _CONTROLLER_ID,
        "role": "host_admin",
        "reset_epoch": 0,
    }
    if owner_id is not None:
        principal["owner_id"] = owner_id

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


async def test_the_list_says_which_one_is_the_phone_asking(
    tmp_path, monkeypatch
) -> None:
    """Before revoking one, a person has to know which row is the phone in
    their hand. Computed from the session that asked — never stored, so two
    answers cannot disagree about it."""

    _stub_controller(monkeypatch)
    bootstrap = _Bootstrap()
    transport = httpx.ASGITransport(app=_app(tmp_path, bootstrap))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        anonymous = await client.get(_CONTROLLERS)
        headers = await _authenticate(client)
        answered = await client.get(_CONTROLLERS, headers=headers)

    assert anonymous.status_code == 401
    assert answered.status_code == 200
    rows = answered.json()["controllers"]
    assert [row["is_you"] for row in rows] == [True, False]
    assert [row["display_name"] for row in rows] == ["书房的手机", "客厅的平板"]
    # Asked as the phone that is asking, not as an Owner: this question is about
    # who may manage the Host, which a Host without a Workspace still has.
    assert bootstrap.asked == [("list", {"controller_id": _CONTROLLER_ID})]


async def test_a_public_key_is_not_something_a_person_is_shown(
    tmp_path, monkeypatch
) -> None:
    """A key is how the Host recognises a phone, not how a person does. Handing
    every controller's key to every other controller spends authority material
    to draw a list; the fingerprint is what pairing shows, so it is what this
    shows."""

    _stub_controller(monkeypatch)
    transport = httpx.ASGITransport(app=_app(tmp_path, _Bootstrap()))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        answered = await client.get(_CONTROLLERS, headers=headers)

    row = answered.json()["controllers"][0]
    assert "public_key" not in row
    assert row["fingerprint"] == "sha256:ab12"


async def test_inviting_answers_with_the_code_and_its_deadline(
    tmp_path, monkeypatch
) -> None:
    """The code is a secret with a deadline, so the deadline travels with it —
    and the Host's internal session id does not, because it is not for a
    person."""

    _stub_controller(monkeypatch)
    bootstrap = _Bootstrap()
    transport = httpx.ASGITransport(app=_app(tmp_path, bootstrap))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        opened = await client.post(
            f"{_CONTROLLERS}/invitations", json={"ttl_seconds": 600}, headers=headers
        )

    assert opened.status_code == 200
    assert opened.json() == {
        "contract_version": "1",
        "setup_code": "1234-5678",
        "expires_at": "2026-08-25T10:00:00Z",
    }
    assert bootstrap.asked == [
        ("invite", {"controller_id": _CONTROLLER_ID, "ttl_seconds": 600})
    ]


async def test_revoking_says_which_phone_was_signed_out(tmp_path, monkeypatch) -> None:
    _stub_controller(monkeypatch)
    bootstrap = _Bootstrap()
    transport = httpx.ASGITransport(app=_app(tmp_path, bootstrap))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        headers = await _authenticate(client)
        withdrawn = await client.delete(f"{_CONTROLLERS}/{_OTHER_ID}", headers=headers)

    assert withdrawn.status_code == 200
    assert withdrawn.json()["controller_id"] == _OTHER_ID
    assert withdrawn.json()["is_you"] is False
    assert bootstrap.asked == [
        ("revoke", {"controller_id": _CONTROLLER_ID, "target_id": _OTHER_ID})
    ]
