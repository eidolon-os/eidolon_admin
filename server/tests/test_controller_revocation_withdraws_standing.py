"""Revoking a phone ends what it vouched for, before the grant goes.

A voucher this Host signs carries its Controller's standing to Hub, and Hub
admits the device that presents it in that Controller's name — for as long as
the voucher is unused. Revoking the phone therefore has to reach Hub too, or a
phone revoked as lost keeps admitting the devices it vouched for while it was
trusted.

What the tests hold:

- revocation tells Hub first, naming the phone being revoked and spoken by the
  phone that asked;
- if Hub cannot be told, the grant is not revoked — the other order leaves a
  window in which the phone is gone and its vouchers still admit;
- a Host that has never been able to sign a voucher (no Workspace) asks
  nothing of Hub and still revokes, because a Host must always be able to
  drop a phone.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from eidolon_admin_server.app.control_plane.contracts import ControllerWithdrawn
from eidolon_admin_server.local_api.app import create_app
from eidolon_admin_server.local_api.device_admissions import DeviceAdmissionError
from tests.controller_session_support import stub_controller_session
from tests.test_local_device_admission_routes import (
    _AUTH_CHALLENGE,
    _BUSINESS_OWNER,
    _CONTROLLER_ID,
    _OWNER_DOMAIN,
    _settings,
)

pytestmark = pytest.mark.asyncio

_LOST_PHONE = "ectrl-ffffffffffffffffffff"
_CONTROLLERS = "/api/management/v1/controllers"


class _Unused:
    def __getattr__(self, name):
        async def unused(*args, **kwargs):
            raise AssertionError(f"nothing here should be called: {name}")

        return unused


class _Ledger:
    """One list both doubles write to, so the order between them is observable."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []


class _Directory:
    def __init__(self, ledger: _Ledger) -> None:
        self._ledger = ledger

    async def list_controllers(self, controller_id: str) -> dict:
        return {"controllers": []}

    async def revoke_controller(self, controller_id: str, target_id: str) -> dict:
        self._ledger.events.append(
            ("revoke", {"controller_id": controller_id, "target_id": target_id})
        )
        return {
            "controller": {
                "controller_id": target_id,
                "public_key": "MCowBQYDK2VwAyEA" + "a" * 28,
                "public_key_fingerprint": "sha256:ab12",
                "role": "host_admin",
                "display_name": "丢了的手机",
                "platform": "android",
                "reset_epoch": 0,
                "created_at": "2026-10-10T09:00:00Z",
                "revoked_at": "2026-10-10T10:00:00Z",
            }
        }


class _Admission:
    def __init__(self, ledger: _Ledger, *, fail: bool = False) -> None:
        self._ledger = ledger
        self._fail = fail

    async def withdraw_controller(self, *, payload):
        self._ledger.events.append(
            (
                "withdraw",
                {
                    "asked_by": payload.actor.principal_id,
                    "principal_id": payload.principal_id,
                    "owner_domain_id": str(payload.owner_domain_id),
                },
            )
        )
        if self._fail:
            raise DeviceAdmissionError("Hub is unavailable", status_code=503)
        return ControllerWithdrawn(
            owner_domain_id=payload.owner_domain_id,
            principal_id=payload.principal_id,
            withdrawn_at=datetime(2026, 10, 10, 10, 0, tzinfo=UTC),
        )

    async def close(self) -> None:
        return None


def _app(tmp_path: Path, ledger: _Ledger, *, fail: bool = False):
    unused = _Unused()
    return create_app(
        _settings(tmp_path),
        workspace_client=unused,  # type: ignore[arg-type]
        runtime_client=unused,  # type: ignore[arg-type]
        devices_client=unused,  # type: ignore[arg-type]
        device_admission_client=_Admission(ledger, fail=fail),  # type: ignore[arg-type]
        host_services_client=unused,  # type: ignore[arg-type]
        management_backend=unused,
        controller_directory=_Directory(ledger),
    )


def _stub(monkeypatch, *, owner_id: str | None) -> None:
    principal = {
        "contract_version": "1",
        "controller_id": _CONTROLLER_ID,
        "role": "host_admin",
        "reset_epoch": 0,
    }
    if owner_id is not None:
        principal["owner_id"] = owner_id
    stub_controller_session(monkeypatch, principal)


async def _revoke(client: httpx.AsyncClient, target: str) -> httpx.Response:
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
    assert session.status_code == 200, session.text
    headers = {"Authorization": f"Bearer {session.json()['access_token']}"}
    return await client.delete(f"{_CONTROLLERS}/{target}", headers=headers)


async def test_revocation_tells_hub_before_the_grant_goes(tmp_path, monkeypatch) -> None:
    _stub(monkeypatch, owner_id=_BUSINESS_OWNER)
    ledger = _Ledger()
    transport = httpx.ASGITransport(app=_app(tmp_path, ledger))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        revoked = await _revoke(client, _LOST_PHONE)

    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["controller_id"] == _LOST_PHONE
    assert ledger.events == [
        (
            "withdraw",
            {
                "asked_by": _CONTROLLER_ID,
                "principal_id": _LOST_PHONE,
                "owner_domain_id": _OWNER_DOMAIN,
            },
        ),
        ("revoke", {"controller_id": _CONTROLLER_ID, "target_id": _LOST_PHONE}),
    ]


async def test_a_phone_may_withdraw_itself(tmp_path, monkeypatch) -> None:
    _stub(monkeypatch, owner_id=_BUSINESS_OWNER)
    ledger = _Ledger()
    transport = httpx.ASGITransport(app=_app(tmp_path, ledger))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        revoked = await _revoke(client, _CONTROLLER_ID)

    assert revoked.status_code == 200, revoked.text
    assert [name for name, _ in ledger.events] == ["withdraw", "revoke"]
    assert ledger.events[0][1]["principal_id"] == _CONTROLLER_ID


async def test_if_hub_cannot_be_told_the_grant_is_not_revoked(tmp_path, monkeypatch) -> None:
    _stub(monkeypatch, owner_id=_BUSINESS_OWNER)
    ledger = _Ledger()
    transport = httpx.ASGITransport(app=_app(tmp_path, ledger, fail=True))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        refused = await _revoke(client, _LOST_PHONE)

    assert refused.status_code == 503
    assert [name for name, _ in ledger.events] == ["withdraw"]


async def test_a_host_with_no_workspace_revokes_without_asking_hub(
    tmp_path, monkeypatch
) -> None:
    _stub(monkeypatch, owner_id=None)
    ledger = _Ledger()
    transport = httpx.ASGITransport(app=_app(tmp_path, ledger, fail=True))
    async with httpx.AsyncClient(transport=transport, base_url="https://local.test") as client:
        revoked = await _revoke(client, _LOST_PHONE)

    assert revoked.status_code == 200, revoked.text
    assert [name for name, _ in ledger.events] == ["revoke"]
