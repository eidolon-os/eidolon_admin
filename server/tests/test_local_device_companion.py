"""Which Companion answers through a device — the other half of adding one.

A device can be mounted and answer as nobody: that is the state it occupies
between being claimed and being put to use, and it is where the product left
every device it ever added, because Kernel could bind a Companion and nothing
above it could ask. These pin the surface a Controller uses to say so, and the
compare-and-swap that keeps two phones from taking turns unnoticed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from eidolon_sdk.device_foundation.v1 import (
    AuthorityEndpoint,
    ClaimPage,
    LogicalAuthority,
    OwnerDomainDescriptor,
)

from eidolon_admin_server.app.control_plane.contracts import (
    ControllerCompanionAttachment,
    KernelMount,
    KernelMountPage,
)
from eidolon_admin_server.bootstrap.config import BootstrapMode, BootstrapSettings
from eidolon_admin_server.bootstrap.control import BootstrapControlClient
from eidolon_admin_server.local_api.app import create_app
from eidolon_admin_server.local_api.device_admissions import DeviceAdmissionError
from eidolon_admin_server.local_api.config import (
    LocalApiSettings,
    VerifiedOwnerDomainOnboardingTarget,
)
from eidolon_admin_server.local_api.devices import DeviceInventoryError

pytestmark = pytest.mark.asyncio

_AUTH_CHALLENGE = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFG"
_CONTROLLER_ID = "ectrl-0123456789abcdefabcd"
_OWNER_DOMAIN = "owner-b0a862b0aab941d64554"
_BUSINESS_OWNER = "owner_683f0000000000000000"
_DEVICE = "device-instance-" + "c" * 64
_NOW = datetime(2026, 8, 25, tzinfo=UTC)


def _descriptor() -> OwnerDomainDescriptor:
    return OwnerDomainDescriptor(
        owner_domain_id=_OWNER_DOMAIN,
        owner_domain_generation=3,
        directory_revision=2,
        descriptor_uri=(
            "https://eidolon-hub.local:9443/api/device-onboarding/v1/descriptor"
        ),
        trust_root_refs=("sha256:" + "b" * 64,),
        endpoints=(
            AuthorityEndpoint(
                authority=LogicalAuthority.ADMISSION,
                logical_audience="eidolon-hub",
                uri="https://eidolon-hub.local:9443/api/admission/v1",
                transport_profile="https-json",
                priority=0,
            ),
        ),
        issued_at=_NOW,
        expires_at=datetime(2027, 8, 25, tzinfo=UTC),
        signing_key_id="sha256:" + "c" * 64,
        signature="s" * 86,
    )


def _mount(*, companion_id: str | None, revision: int) -> dict:
    return {
        "operation": "kernel.device-mount",
        "device_id": _DEVICE,
        "owner_id": _BUSINESS_OWNER,
        "device_ref": {
            "device_instance_id": _DEVICE,
            "owner_domain_id": _OWNER_DOMAIN,
            "owner_domain_generation": 3,
            "claim_generation": 2,
            "trust_epoch": 1,
        },
        "attached_companion_id": companion_id,
        "revision": revision,
        "created_at": _NOW.isoformat(),
        "updated_at": _NOW.isoformat(),
        "request_id": "internal-request-not-for-mobile",
        "fingerprint": "sha256:" + "0" * 64,
        "active": True,
    }


def _claims() -> ClaimPage:
    return ClaimPage.model_validate(
        {
            "owner_domain_id": _OWNER_DOMAIN,
            "items": [
                {
                    "device_ref": {
                        "device_instance_id": _DEVICE,
                        "owner_domain_id": _OWNER_DOMAIN,
                        "owner_domain_generation": 3,
                        "claim_generation": 2,
                        "trust_epoch": 1,
                    },
                    "business_owner_id": _BUSINESS_OWNER,
                    "manifest_ref": {
                        "manifest_id": "box3-device-manifest",
                        "revision": 1,
                        "digest": "sha256:" + "a" * 64,
                    },
                    "state": "active",
                    "revision": 1,
                    "updated_at": _NOW.isoformat(),
                }
            ],
            "next_cursor": None,
            "observed_at": _NOW.isoformat(),
        }
    )


class _Devices:
    """Kernel membership, as the control plane answers it."""

    def __init__(self, *, refuse: DeviceInventoryError | None = None) -> None:
        self.companion_id: str | None = None
        self.revision = 1
        self.commands: list[ControllerCompanionAttachment] = []
        self.refuse = refuse

    async def list_mounts(self, owner_id: str) -> KernelMountPage:
        return KernelMountPage.model_validate(
            {
                "operation": "kernel.device-mount-page",
                "next_cursor": None,
                "mounts": [
                    _mount(companion_id=self.companion_id, revision=self.revision)
                ],
            }
        )

    async def set_companion(self, *, payload) -> KernelMount:
        self.commands.append(payload)
        if self.refuse is not None:
            raise self.refuse
        self.companion_id = payload.companion_id
        self.revision = payload.expected_revision + 1
        return KernelMount.model_validate(
            _mount(companion_id=self.companion_id, revision=self.revision)
        )

    async def close(self) -> None:
        return None


class _Admission:
    async def query_claims(self, *, payload) -> ClaimPage:
        return _claims()

    async def close(self) -> None:
        return None

    def __getattr__(self, name):
        raise AssertionError(f"unexpected admission call: {name}")


class _UnusedPort:
    def __getattr__(self, name):
        raise AssertionError(f"unexpected client call: {name}")

    async def close(self) -> None:
        return None


def _app(tmp_path: Path, devices, admission=None):
    root = tmp_path / "owner-root.pem"
    signer = tmp_path / "authority-signer.pem"
    for path in (root, signer):
        path.write_text("-----BEGIN CERTIFICATE-----\nMA==\n", encoding="ascii")
    settings = LocalApiSettings(
        bootstrap=BootstrapSettings(
            mode=BootstrapMode.DEVELOPMENT,
            state_dir=tmp_path / "state",
            runtime_dir=tmp_path / "run",
            control_socket=tmp_path / "run/control.sock",
            ble_service_uuid="179e2e95-b1ee-5aa5-8dcf-7519b6c7ac52",
        ),
        device_onboarding_target=VerifiedOwnerDomainOnboardingTarget(
            owner_domain_id=_OWNER_DOMAIN,
            descriptor_uri="https://eidolon-hub.local:9443/.well-known/eidolon-owner-domain",
            descriptor=_descriptor(),
            owner_root_certificate_path=root,
            authority_signing_certificate_path=signer,
        ),
    )
    unused = _UnusedPort()
    return create_app(
        settings,
        workspace_client=unused,  # type: ignore[arg-type]
        runtime_client=unused,  # type: ignore[arg-type]
        devices_client=devices,  # type: ignore[arg-type]
        device_admission_client=admission or _Admission(),  # type: ignore[arg-type]
        host_services_client=unused,  # type: ignore[arg-type]
    )


async def _headers(client: httpx.AsyncClient) -> dict[str, str]:
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
    return {"Authorization": f"Bearer {session.json()['access_token']}"}


def _controller_principal(monkeypatch: pytest.MonkeyPatch) -> None:
    principal = {
        "contract_version": "1",
        "controller_id": _CONTROLLER_ID,
        "owner_id": _BUSINESS_OWNER,
        "reset_epoch": 0,
    }

    async def bootstrap_request(self, operation: str, **_parameters):
        if operation in {"controller.authenticate", "controller.validate"}:
            return principal
        raise AssertionError(f"unexpected bootstrap operation: {operation}")

    monkeypatch.setattr(BootstrapControlClient, "request", bootstrap_request)


async def test_a_device_can_be_bound_to_a_companion_and_released(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _controller_principal(monkeypatch)
    devices = _Devices()
    transport = httpx.ASGITransport(app=_app(tmp_path, devices))
    async with httpx.AsyncClient(
        transport=transport, base_url="https://local.test"
    ) as client:
        headers = await _headers(client)
        before = await client.get("/api/management/v1/devices", headers=headers)
        bound = await client.put(
            f"/api/management/v1/devices/{_DEVICE}/companion",
            headers=headers,
            json={
                "request_id": "attach-1",
                "companion_id": "c_01",
                "expected_revision": 1,
            },
        )
        released = await client.put(
            f"/api/management/v1/devices/{_DEVICE}/companion",
            headers=headers,
            json={
                "request_id": "detach-1",
                "companion_id": None,
                "expected_revision": 2,
            },
        )

    assert before.json()["devices"][0]["answers_as_companion_id"] is None
    assert bound.status_code == 200
    assert bound.json()["answers_as_companion_id"] == "c_01"
    assert released.status_code == 200
    assert released.json()["answers_as_companion_id"] is None

    # One command, two values — and the Owner's compare-and-swap travels with it.
    assert [command.companion_id for command in devices.commands] == ["c_01", None]
    assert [command.expected_revision for command in devices.commands] == [1, 2]
    assert all(command.owner_id == _BUSINESS_OWNER for command in devices.commands)


async def test_binding_a_device_this_owner_does_not_hold_is_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _controller_principal(monkeypatch)
    devices = _Devices()
    transport = httpx.ASGITransport(app=_app(tmp_path, devices))
    async with httpx.AsyncClient(
        transport=transport, base_url="https://local.test"
    ) as client:
        headers = await _headers(client)
        anonymous = await client.put(
            f"/api/management/v1/devices/{_DEVICE}/companion",
            json={
                "request_id": "attach-1",
                "companion_id": "c_01",
                "expected_revision": 1,
            },
        )
        missing = await client.put(
            "/api/management/v1/devices/device-instance-someone-elses/companion",
            headers=headers,
            json={
                "request_id": "attach-2",
                "companion_id": "c_01",
                "expected_revision": 1,
            },
        )

    assert anonymous.status_code == 401
    assert missing.status_code == 404
    assert devices.commands == []


async def test_a_stale_revision_is_the_owner_s_refusal_not_a_silent_win(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _controller_principal(monkeypatch)
    devices = _Devices(
        refuse=DeviceInventoryError("Kernel refused the revision", status_code=409)
    )
    transport = httpx.ASGITransport(app=_app(tmp_path, devices))
    async with httpx.AsyncClient(
        transport=transport, base_url="https://local.test"
    ) as client:
        headers = await _headers(client)
        stale = await client.put(
            f"/api/management/v1/devices/{_DEVICE}/companion",
            headers=headers,
            json={
                "request_id": "attach-1",
                "companion_id": "c_01",
                "expected_revision": 1,
            },
        )

    assert stale.status_code == 409
    assert len(devices.commands) == 1


@pytest.mark.asyncio
async def test_a_refused_removal_answers_with_the_refusal_not_a_five_hundred(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The conversion at the route, not the two functions it calls.

    ``device_admission_detail`` answers a structured body when a reason is set
    and a plain string otherwise, and the refusal builder truncates what it is
    given. So passing the body where the reason belongs raises TypeError the
    moment anything sets a reason — turning a refusal the caller could act on
    into a 500. It stayed unreachable only because nothing on the removal path
    set a reason, which was the other half of the same defect. Unit tests over
    the two functions cannot see this: what is wrong is which one the route
    calls.
    """

    _controller_principal(monkeypatch)

    class _RefusingAdmission(_Admission):
        async def remove(self, **_values):
            raise DeviceAdmissionError(
                "owner authorization was refused by the Hub",
                status_code=403,
                reason="主机不再授权这台手机管理设备。",
            )

    transport = httpx.ASGITransport(
        app=_app(tmp_path, _Devices(), admission=_RefusingAdmission())
    )
    async with httpx.AsyncClient(
        transport=transport, base_url="https://local.test"
    ) as client:
        headers = await _headers(client)
        refused = await client.post(
            f"/api/management/v1/devices/{_DEVICE}/removal",
            headers=headers,
            json={"request_id": "removal-refused-1"},
        )

    assert refused.status_code == 403, refused.text
    # The published refusal envelope, not a shrug and not a 500.
    detail = refused.json()["detail"]
    assert detail["reason"] == "主机不再授权这台手机管理设备。"
    assert detail["kind"] == "denied"
