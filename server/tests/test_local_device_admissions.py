from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import NameOID

from eidolon_admin_server.app.control_plane.contracts import (
    ControllerDeviceAdmissionRequest,
    ControllerDeviceRemovalRequest,
    DeviceAdmissionResult,
    DeviceRemovalResult,
    HubDevicePage,
    HubLifecycleStatus,
    KernelMount,
    OwnerInventory,
    WorkflowStep,
)
from eidolon_admin_server.bootstrap.config import BootstrapMode, BootstrapSettings
from eidolon_admin_server.bootstrap.control import BootstrapControlClient
from eidolon_admin_server.local_api.app import create_app
from eidolon_admin_server.local_api.config import (
    LocalApiSettings,
    VerifiedHubOnboardingTarget,
    load_local_api_settings,
)
from eidolon_admin_server.local_api.device_admissions import (
    AdminDeviceAdmissionClient,
    DeviceAdmissionError,
    device_admission_detail,
    device_admission_progress,
    device_removal_progress,
)


_AUTH_CHALLENGE = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFG"
_CONTROLLER_ID = "ectrl-0123456789abcdefabcd"


def _write_certificate(path: Path, hostname: str = "eidolon-hub.local") -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.now(UTC)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=30))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(hostname)]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    path.write_bytes(certificate.public_bytes(Encoding.PEM))


def _bootstrap(tmp_path: Path) -> BootstrapSettings:
    return BootstrapSettings(
        mode=BootstrapMode.DEVELOPMENT,
        state_dir=tmp_path / "state",
        runtime_dir=tmp_path / "run",
        control_socket=tmp_path / "run/control.sock",
        ble_service_uuid="179e2e95-b1ee-5aa5-8dcf-7519b6c7ac52",
    )


def _target(certificate: Path) -> VerifiedHubOnboardingTarget:
    settings = load_local_api_settings(
        {
            "EIDOLON_BOOTSTRAP_MODE": "development",
            "EIDOLON_BOOTSTRAP_STATE_DIR": str(certificate.parent / "state"),
            "EIDOLON_BOOTSTRAP_RUNTIME_DIR": str(certificate.parent / "run"),
            "EIDOLON_BOOTSTRAP_CONTROL_SOCKET": "/tmp/eidolon-local-target-test.sock",
            "EIDOLON_LOCAL_API_HUB_ID": "hub-local",
            "EIDOLON_LOCAL_API_HUB_DESCRIPTOR_URI": (
                "https://eidolon-hub.local/api/device-onboarding/v1/descriptor"
            ),
            "EIDOLON_LOCAL_API_HUB_TLS_CERTIFICATE": str(certificate),
        }
    )
    assert settings.device_onboarding_target is not None
    return settings.device_onboarding_target


def _result(*, owner_id: str = "owner-1") -> DeviceAdmissionResult:
    now = datetime.now(UTC)
    mount = KernelMount(
        operation="kernel.device-mount",
        device_id="device-authoritative",
        owner_id=owner_id,
        attached_companion_id="companion-1",
        revision=2,
        created_at=now,
        updated_at=now,
        request_id="admin:approval:kernel-attach",
        fingerprint="sha256:" + "a" * 64,
        active=True,
    )
    return DeviceAdmissionResult(
        request_id="device-approval-1",
        outcome="completed",
        completed_stage="companion_attached",
        steps=(
            WorkflowStep(name="hub_approval", state="committed"),
            WorkflowStep(name="kernel_mount", state="committed", revision=1),
            WorkflowStep(
                name="companion_attachment", state="committed", revision=2
            ),
        ),
        hub=HubLifecycleStatus(
            operation="device.lifecycle-status",
            device_id="device-authoritative",
            owner_id=owner_id,
            lifecycle_state="approved",
        ),
        mount=mount,
    )


def _removal(*, owner_id: str = "owner-1") -> DeviceRemovalResult:
    return DeviceRemovalResult(
        request_id="device-removal-1",
        outcome="completed",
        completed_stage="kernel_unmounted",
        steps=(
            WorkflowStep(name="hub_revocation", state="committed"),
            WorkflowStep(name="kernel_unmount", state="committed", revision=2),
        ),
        hub=HubLifecycleStatus(
            operation="device.lifecycle-status",
            device_id="device-authoritative",
            owner_id=owner_id,
            lifecycle_state="revoked",
        ),
    )


def test_hub_target_spki_is_derived_from_hostname_bound_leaf_certificate(
    tmp_path: Path,
) -> None:
    certificate = tmp_path / "hub-leaf.pem"
    _write_certificate(certificate)

    target = _target(certificate)

    assert target.hub_id == "hub-local"
    assert target.tls_certificate_path == certificate.resolve()
    assert target.tls_spki_fingerprint.startswith("sha256:")
    assert len(target.tls_spki_fingerprint) == 50


def test_hub_target_rejects_descriptor_hostname_not_in_certificate(
    tmp_path: Path,
) -> None:
    certificate = tmp_path / "other-leaf.pem"
    _write_certificate(certificate, hostname="other.local")

    with pytest.raises(ValueError, match="not present"):
        _target(certificate)


@pytest.mark.asyncio
async def test_local_admin_adapter_forwards_exact_approval_contract() -> None:
    observed: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        observed.update(json.loads(request.content))
        assert request.method == "PUT"
        assert request.url.raw_path == (
            b"/api/control-plane/v1/local-device-admissions/device-authoritative"
        )
        assert request.headers["authorization"] == "Bearer local-service-token"
        return httpx.Response(200, json=_result().model_dump(mode="json"))

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    subject = AdminDeviceAdmissionClient(
        base_url="http://127.0.0.1:9000",
        service_token="local-service-token",
        timeout_seconds=1,
        client=http_client,
    )
    payload = ControllerDeviceAdmissionRequest(
        contract_version="1",
        request_id="device-approval-1",
        owner_id="owner-1",
        controller_id=_CONTROLLER_ID,
        device_id="device-authoritative",
        companion_id="companion-1",
    )
    try:
        result = await subject.claim(payload=payload)
    finally:
        await http_client.aclose()

    assert result.hub is not None
    assert result.hub.device_id == "device-authoritative"
    assert observed["device_id"] == "device-authoritative"
    assert "owner_id" in observed


@pytest.mark.asyncio
async def test_a_refused_admission_says_why_and_keeps_the_authority_words_here(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The Host is told which authority refused and in what way. Answering the
    # phone with one generic sentence for every status is what left this
    # undiagnosable: the Owner is asked to refresh a list that will never
    # change, and nothing anywhere records the reason.
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409,
            json={
                "detail": {
                    "authority": "hub",
                    "kind": "conflict",
                    "detail": "management request_id was reused",
                    "upstream_status": 409,
                    "retryable": False,
                }
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    subject = AdminDeviceAdmissionClient(
        base_url="http://127.0.0.1:9000",
        service_token="local-service-token",
        timeout_seconds=1,
        client=http_client,
    )
    payload = ControllerDeviceAdmissionRequest(
        contract_version="1",
        request_id="device-approval-1",
        owner_id="owner-1",
        controller_id=_CONTROLLER_ID,
        device_id="24:ec:4a:52:f3:54",
    )
    try:
        with caplog.at_level(logging.WARNING):
            with pytest.raises(DeviceAdmissionError) as caught:
                await subject.claim(payload=payload)
    finally:
        await http_client.aclose()

    assert caught.value.status_code == 409
    assert caught.value.reason is not None
    assert "主机不接受这台设备当前的状态" in caught.value.reason
    # The authority's own words name internal request identifiers, so they stay
    # on the Host rather than travelling to a screen.
    assert "request_id" not in caught.value.reason
    assert "management request_id was reused" in caplog.text
    assert "hub" in caplog.text
    # Tagged, so App can tell a sentence written for the Owner apart from the
    # diagnostics this module raises everywhere else.
    assert device_admission_detail(caught.value) == {"reason": caught.value.reason}


@pytest.mark.asyncio
async def test_a_refusal_admin_did_not_grade_offers_the_owner_nothing_to_show(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Not every refusal comes with an authority failure to grade. What is left
    # is a diagnostic naming authorities and contracts, which is for whoever
    # reads the Host — so it must reach App untagged, leaving App to say
    # something in its own words rather than putting this on a screen.
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"detail": "device admission path and body do not match"})

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    subject = AdminDeviceAdmissionClient(
        base_url="http://127.0.0.1:9000",
        service_token="local-service-token",
        timeout_seconds=1,
        client=http_client,
    )
    payload = ControllerDeviceAdmissionRequest(
        contract_version="1",
        request_id="device-approval-1",
        owner_id="owner-1",
        controller_id=_CONTROLLER_ID,
        device_id="24:ec:4a:52:f3:54",
    )
    try:
        with caplog.at_level(logging.WARNING):
            with pytest.raises(DeviceAdmissionError) as caught:
                await subject.claim(payload=payload)
    finally:
        await http_client.aclose()

    assert caught.value.status_code == 409
    assert caught.value.reason is None
    detail = device_admission_detail(caught.value)
    assert isinstance(detail, str)
    assert "Admin Device admission" in detail
    # The Host still records that it happened, so this is diagnosable from here.
    assert "no authority failure" in caplog.text


def test_mobile_progress_uses_hub_authoritative_device_identity() -> None:
    progress = device_admission_progress(
        owner_id="owner-1",
        companion_id="companion-1",
        result=_result(),
    )

    assert progress.device_id == "device-authoritative"
    assert progress.state == "ready"
    assert progress.completed_stage == "companion-attached"


class _UnusedPort:
    async def close(self) -> None:
        return None


class _OwnedDevicesPort:
    """A Host that holds exactly one device, mounted to the session's Owner."""

    def __init__(self, device_id: str = "device-authoritative") -> None:
        self.device_id = device_id

    async def list_inventory(self, owner_id: str, controller_id: str):
        return OwnerInventory.model_validate(
            {
                "operation": "admin.owner-device-inventory",
                "owner_id": owner_id,
                "degraded": False,
                "hub": {"state": "ok", "latency_ms": 1.0},
                "kernel": {"state": "ok", "latency_ms": 1.0},
                "devices": [],
                "mounts": [
                    {
                        "operation": "kernel.device-mount",
                        "device_id": self.device_id,
                        "owner_id": owner_id,
                        "attached_companion_id": None,
                        "revision": 1,
                        "created_at": "2026-08-09T08:00:00Z",
                        "updated_at": "2026-08-09T08:00:00Z",
                        "request_id": "seed",
                        "fingerprint": "sha256:" + "0" * 64,
                        "active": True,
                    }
                ],
            }
        )

    async def close(self) -> None:
        return None


class _AdmissionPort:
    payload: ControllerDeviceAdmissionRequest | None = None
    removal: ControllerDeviceRemovalRequest | None = None

    async def claim(self, *, payload: ControllerDeviceAdmissionRequest):
        self.payload = payload
        return _result(owner_id=payload.owner_id)

    async def remove(self, *, payload: ControllerDeviceRemovalRequest):
        self.removal = payload
        return _removal(owner_id=payload.owner_id)

    async def list_pending(self, *, controller_id: str) -> HubDevicePage:
        assert controller_id == _CONTROLLER_ID
        return HubDevicePage.model_validate(
            {
                "operation": "device.directory-page",
                "next_cursor": None,
                "devices": [
                    {
                        "operation": "device.directory-entry",
                        "device_id": "device-authoritative",
                        "owner_scope": "unclaimed",
                        "display_name": "Desk Device",
                        "device_kind": "voice-client",
                        "manifest": {
                            "schema_version": 1,
                            "title": "Desk Device",
                            "properties": [],
                            "actions": [],
                            "events": [],
                            "media": [],
                        },
                        "manifest_revision": "sha256:" + "a" * 64,
                        "lifecycle_state": "pending-approval",
                        "enrolled_at": datetime.now(UTC).isoformat(),
                        "updated_at": datetime.now(UTC).isoformat(),
                    }
                ],
            }
        )

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_mobile_contract_is_controller_authenticated_and_owner_derived(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    certificate = tmp_path / "hub-leaf.pem"
    _write_certificate(certificate)
    target = _target(certificate)
    principal = {
        "contract_version": "1",
        "controller_id": _CONTROLLER_ID,
        "owner_id": "owner-derived",
        "reset_epoch": 0,
    }

    async def bootstrap_request(self, operation: str, **_parameters):
        if operation in {"controller.authenticate", "controller.validate"}:
            return principal
        raise AssertionError(f"unexpected bootstrap operation: {operation}")

    monkeypatch.setattr(BootstrapControlClient, "request", bootstrap_request)
    admission = _AdmissionPort()
    unused = _UnusedPort()
    app = create_app(
        LocalApiSettings(
            bootstrap=_bootstrap(tmp_path),
            device_onboarding_target=target,
        ),
        workspace_client=unused,  # type: ignore[arg-type]
        runtime_client=unused,  # type: ignore[arg-type]
        devices_client=_OwnedDevicesPort(),  # type: ignore[arg-type]
        device_admission_client=admission,  # type: ignore[arg-type]
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://local.test",
    ) as client:
        unauthenticated = await client.get("/api/local/v1/device-onboarding/target")
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
        token = session.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        target_response = await client.get(
            "/api/local/v1/device-onboarding/target",
            headers=headers,
        )
        pending = await client.get(
            "/api/local/v1/device-enrollments/pending",
            headers=headers,
        )
        admitted = await client.post(
            "/api/local/v1/device-enrollments/device-authoritative/approval",
            headers=headers,
            json={
                "contract_version": "1",
                "request_id": "device-approval-1",
                "companion_id": "companion-1",
            },
        )
        injected_owner = await client.post(
            "/api/local/v1/device-enrollments/device-authoritative/approval",
            headers=headers,
            json={
                "contract_version": "1",
                "request_id": "device-approval-1",
                "owner_id": "owner-mobile-chosen",
            },
        )

    assert unauthenticated.status_code == 401
    assert session.status_code == 200
    assert target_response.status_code == 200
    assert pending.status_code == 200
    assert pending.json()["devices"][0]["device_id"] == "device-authoritative"
    assert target_response.json() == {
        "operation": "local.device-onboarding-target",
        "contract_version": "1",
        "hub_id": "hub-local",
        "descriptor_uri": (
            "https://eidolon-hub.local/api/device-onboarding/v1/descriptor"
        ),
        "tls_spki_fingerprint": target.tls_spki_fingerprint,
        # The Controller carries this to a device being set up: a device cannot
        # obtain the Host's certificate from anywhere it could already trust.
        "hub_certificate": certificate.read_text(encoding="utf-8"),
    }
    assert admitted.status_code == 200
    assert injected_owner.status_code == 422
    assert admitted.json()["owner_id"] == "owner-derived"
    assert admitted.json()["device_id"] == "device-authoritative"
    assert admission.payload is not None
    assert admission.payload.owner_id == "owner-derived"
    assert admission.payload.controller_id == _CONTROLLER_ID


class _RefusingAdmissionPort(_AdmissionPort):
    def __init__(self, error: DeviceAdmissionError) -> None:
        self._error = error

    async def claim(self, *, payload: ControllerDeviceAdmissionRequest):
        raise self._error


@pytest.mark.asyncio
async def test_only_a_graded_reason_leaves_the_host_tagged_for_a_screen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # App cannot tell a sentence written for the Owner from a diagnostic written
    # for whoever reads the Host by looking at it, so the wire has to say which
    # it is. Untagged, App falls back to its own words; that is what keeps
    # contract-violation wording off a screen.
    certificate = tmp_path / "hub-leaf.pem"
    _write_certificate(certificate)
    target = _target(certificate)
    principal = {
        "contract_version": "1",
        "controller_id": _CONTROLLER_ID,
        "owner_id": "owner-derived",
        "reset_epoch": 0,
    }

    async def bootstrap_request(self, operation: str, **_parameters):
        if operation in {"controller.authenticate", "controller.validate"}:
            return principal
        raise AssertionError(f"unexpected bootstrap operation: {operation}")

    monkeypatch.setattr(BootstrapControlClient, "request", bootstrap_request)

    async def refuse(error: DeviceAdmissionError) -> httpx.Response:
        unused = _UnusedPort()
        app = create_app(
            LocalApiSettings(
                bootstrap=_bootstrap(tmp_path),
                device_onboarding_target=target,
            ),
            workspace_client=unused,  # type: ignore[arg-type]
            runtime_client=unused,  # type: ignore[arg-type]
            devices_client=unused,  # type: ignore[arg-type]
            device_admission_client=_RefusingAdmissionPort(error),  # type: ignore[arg-type]
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://local.test",
        ) as client:
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
            return await client.post(
                "/api/local/v1/device-enrollments/24%3Aec%3A4a%3A52%3Af3%3A54/approval",
                headers={"Authorization": f"Bearer {session.json()['access_token']}"},
                json={"contract_version": "1", "request_id": "device-approval-1"},
            )

    graded = await refuse(
        DeviceAdmissionError(
            "Admin Device admission did not complete the requested transition",
            status_code=409,
            reason="主机上已经没有这台设备了。",
        )
    )
    diagnostic = await refuse(
        DeviceAdmissionError(
            "Admin Device admission response violated its contract",
            status_code=502,
        )
    )

    assert graded.status_code == 409
    assert graded.json()["detail"] == {"reason": "主机上已经没有这台设备了。"}
    assert diagnostic.status_code == 502
    assert diagnostic.json()["detail"] == (
        "Admin Device admission response violated its contract"
    )


@pytest.mark.asyncio
async def test_removal_forwards_the_exact_controller_contract() -> None:
    observed: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        observed.update(json.loads(request.content))
        assert request.method == "PUT"
        assert request.url.raw_path == (
            b"/api/control-plane/v1/local-device-removals/device-authoritative"
        )
        assert request.headers["authorization"] == "Bearer local-service-token"
        return httpx.Response(200, json=_removal().model_dump(mode="json"))

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    subject = AdminDeviceAdmissionClient(
        base_url="http://127.0.0.1:9000",
        service_token="local-service-token",
        timeout_seconds=1,
        client=http_client,
    )
    payload = ControllerDeviceRemovalRequest(
        contract_version="1",
        request_id="device-removal-1",
        owner_id="owner-1",
        controller_id=_CONTROLLER_ID,
        device_id="device-authoritative",
    )
    try:
        result = await subject.remove(payload=payload)
    finally:
        await http_client.aclose()

    assert result.hub is not None
    assert result.hub.lifecycle_state == "revoked"
    assert observed["reason"] == "owner-removed"


def test_a_removal_that_only_revoked_is_not_reported_as_removed() -> None:
    # The phone is already off — the Hub committed — but it is still mounted,
    # so calling this "removed" would leave the owner looking at a device that
    # is supposed to be gone.
    partial = DeviceRemovalResult(
        request_id="device-removal-1",
        outcome="retry_required",
        completed_stage="hub_revoked",
        recovery="retry-forward-same-request-id",
        steps=(
            WorkflowStep(name="hub_revocation", state="committed"),
            WorkflowStep(name="kernel_unmount", state="failed"),
        ),
        hub=HubLifecycleStatus(
            operation="device.lifecycle-status",
            device_id="device-authoritative",
            owner_id="owner-1",
            lifecycle_state="revoked",
        ),
    )

    progress = device_removal_progress(
        owner_id="owner-1",
        device_id="device-authoritative",
        result=partial,
    )

    assert progress.state == "revoked"
    assert progress.completed_stage == "hub-revoked"
    assert progress.retryable is True


@pytest.mark.asyncio
async def test_removing_a_device_is_controller_authenticated_and_owner_derived(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    certificate = tmp_path / "hub-leaf.pem"
    _write_certificate(certificate)
    target = _target(certificate)
    principal = {
        "contract_version": "1",
        "controller_id": _CONTROLLER_ID,
        "owner_id": "owner-derived",
        "reset_epoch": 0,
    }

    async def bootstrap_request(self, operation: str, **_parameters):
        if operation in {"controller.authenticate", "controller.validate"}:
            return principal
        raise AssertionError(f"unexpected bootstrap operation: {operation}")

    monkeypatch.setattr(BootstrapControlClient, "request", bootstrap_request)
    admission = _AdmissionPort()
    unused = _UnusedPort()
    app = create_app(
        LocalApiSettings(
            bootstrap=_bootstrap(tmp_path),
            device_onboarding_target=target,
        ),
        workspace_client=unused,  # type: ignore[arg-type]
        runtime_client=unused,  # type: ignore[arg-type]
        devices_client=_OwnedDevicesPort(),  # type: ignore[arg-type]
        device_admission_client=admission,  # type: ignore[arg-type]
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://local.test",
    ) as client:
        unauthenticated = await client.post(
            "/api/local/v1/devices/device-authoritative/removal",
            json={"contract_version": "1", "request_id": "device-removal-1"},
        )
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
        headers = {"Authorization": f"Bearer {session.json()['access_token']}"}
        removed = await client.post(
            "/api/local/v1/devices/device-authoritative/removal",
            headers=headers,
            json={"contract_version": "1", "request_id": "device-removal-1"},
        )
        injected_owner = await client.post(
            "/api/local/v1/devices/device-authoritative/removal",
            headers=headers,
            json={
                "contract_version": "1",
                "request_id": "device-removal-1",
                "owner_id": "owner-mobile-chosen",
            },
        )

    assert unauthenticated.status_code == 401
    assert removed.status_code == 200
    assert injected_owner.status_code == 422
    assert removed.json()["state"] == "removed"
    assert removed.json()["owner_id"] == "owner-derived"
    assert admission.removal is not None
    assert admission.removal.owner_id == "owner-derived"
    assert admission.removal.controller_id == _CONTROLLER_ID


@pytest.mark.asyncio
async def test_a_device_that_is_not_this_owners_cannot_be_removed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An identifier in a path is not authority.

    Removal took a device_id from the path and an owner_id from the session
    and never asked whether they belonged together — not here, not in the
    control plane, and not in the Hub use case underneath, which revokes
    whatever id it is handed. Each layer could reasonably assume one of the
    others had checked, and none had.
    """

    certificate = tmp_path / "hub-leaf.pem"
    _write_certificate(certificate)
    principal = {
        "contract_version": "1",
        "controller_id": _CONTROLLER_ID,
        "owner_id": "owner-derived",
        "reset_epoch": 0,
    }

    async def bootstrap_request(self, operation: str, **_parameters):
        if operation in {"controller.authenticate", "controller.validate"}:
            return principal
        raise AssertionError(f"unexpected bootstrap operation: {operation}")

    monkeypatch.setattr(BootstrapControlClient, "request", bootstrap_request)
    admission = _AdmissionPort()
    app = create_app(
        LocalApiSettings(
            bootstrap=_bootstrap(tmp_path),
            device_onboarding_target=_target(certificate),
        ),
        workspace_client=_UnusedPort(),  # type: ignore[arg-type]
        runtime_client=_UnusedPort(),  # type: ignore[arg-type]
        # This Host holds one device, and it is not the one being asked about.
        devices_client=_OwnedDevicesPort("device-of-someone-else"),  # type: ignore[arg-type]
        device_admission_client=admission,  # type: ignore[arg-type]
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://local.test",
    ) as client:
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
        refused = await client.post(
            "/api/local/v1/devices/device-authoritative/removal",
            headers={"Authorization": f"Bearer {session.json()['access_token']}"},
            json={"contract_version": "1", "request_id": "device-removal-1"},
        )

    # Absent rather than forbidden: whose devices exist is not something a
    # stranger's session gets to learn.
    assert refused.status_code == 404
    # And nothing reached the authorities that would have carried it out.
    assert admission.removal is None
