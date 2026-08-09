from __future__ import annotations

from datetime import UTC, datetime

import jwt
import pytest

from eidolon_admin_server.app.control_plane.contracts import (
    DevicePairingAdmissionRequest,
    HubLifecycleStatus,
    KernelMount,
    KernelMutationResult,
)
from eidolon_admin_server.app.control_plane.errors import AuthorityFailure
from eidolon_admin_server.app.control_plane.hub_credentials import (
    HubOwnerCredentialIssuer,
)
from eidolon_admin_server.app.control_plane.service import ControlPlaneService

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]

_SECRET = b"admin-hub-owner-management-secret-32-bytes"
_ENROLLMENT_ID = "enrollment_abcdefghijklmnopqrstuvwx"
_PAIRING_SECRET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFG"


class _Hub:
    calls: list[dict]

    def __init__(self) -> None:
        self.calls = []

    async def claim_pairing(self, **kwargs) -> HubLifecycleStatus:
        self.calls.append(kwargs)
        return HubLifecycleStatus(
            operation="device.lifecycle-status",
            device_id="device-from-hub",
            owner_id=kwargs["owner_id"],
            lifecycle_state="approved",
        )


class _Kernel:
    def __init__(self) -> None:
        self.mount_calls: list[dict] = []
        self.attach_calls: list[dict] = []
        self.mount_failure: AuthorityFailure | None = None

    async def mount(self, **kwargs) -> KernelMutationResult:
        if self.mount_failure:
            raise self.mount_failure
        self.mount_calls.append(kwargs)
        return KernelMutationResult(
            operation="kernel.device-mount-mutation-result",
            mount=_mount(
                device_id=kwargs["device_id"],
                owner_id=kwargs["owner_id"],
                request_id=kwargs["request_id"],
            ),
            audit_position=1,
            replayed=len(self.mount_calls) > 1,
        )

    async def attach(self, **kwargs) -> KernelMutationResult:
        self.attach_calls.append(kwargs)
        return KernelMutationResult(
            operation="kernel.device-mount-mutation-result",
            mount=_mount(
                device_id=kwargs["device_id"],
                owner_id=kwargs["owner_id"],
                request_id=kwargs["request_id"],
                companion_id=kwargs["companion_id"],
                revision=2,
            ),
            audit_position=2,
            replayed=len(self.attach_calls) > 1,
        )


def _mount(
    *,
    device_id: str,
    owner_id: str,
    request_id: str,
    companion_id: str | None = None,
    revision: int = 1,
) -> KernelMount:
    now = datetime.now(UTC)
    return KernelMount(
        operation="kernel.device-mount",
        device_id=device_id,
        owner_id=owner_id,
        attached_companion_id=companion_id,
        revision=revision,
        created_at=now,
        updated_at=now,
        request_id=request_id,
        fingerprint="sha256:" + "0" * 64,
        active=True,
    )


def _request() -> DevicePairingAdmissionRequest:
    return DevicePairingAdmissionRequest(
        contract_version="1",
        request_id="mobile-claim-1",
        owner_id="owner-1",
        controller_id="ectrl-0123456789abcdefabcd",
        enrollment_id=_ENROLLMENT_ID,
        pairing_secret=_PAIRING_SECRET,
        companion_id="companion-1",
    )


def _service(hub: _Hub, kernel: _Kernel) -> ControlPlaneService:
    return ControlPlaneService(
        directory=object(),  # type: ignore[arg-type]
        data=object(),  # type: ignore[arg-type]
        workspace=object(),  # type: ignore[arg-type]
        hub=hub,  # type: ignore[arg-type]
        kernel=kernel,  # type: ignore[arg-type]
        hub_credentials=HubOwnerCredentialIssuer(secret=_SECRET),
    )


async def test_pairing_admission_mints_owner_scope_and_uses_hub_device_identity() -> None:
    hub, kernel = _Hub(), _Kernel()

    result = await _service(hub, kernel).admit_device_pairing(
        setup_id="setup-1",
        payload=_request(),
    )

    assert result.outcome == "completed"
    assert result.completed_stage == "companion_attached"
    assert result.hub is not None
    assert result.hub.device_id == "device-from-hub"
    assert kernel.mount_calls[0]["device_id"] == "device-from-hub"
    assert kernel.attach_calls[0]["device_id"] == "device-from-hub"
    assert hub.calls[0]["pairing_secret"] == _PAIRING_SECRET
    encoded = hub.calls[0]["authorization"].removeprefix("Bearer ")
    claims = jwt.decode(encoded, _SECRET, algorithms=["HS256"], audience="eidolon-hub")
    assert claims["owner_id"] == "owner-1"
    assert claims["roles"] == ["device-manager"]
    assert claims["sub"] == "eidolon-local-api/ectrl-0123456789abcdefabcd"


async def test_pairing_admission_retry_reuses_all_deterministic_child_ids() -> None:
    hub, kernel = _Hub(), _Kernel()
    kernel.mount_failure = AuthorityFailure(
        "kernel", "unavailable", "kernel down", 503, retryable=True
    )
    service = _service(hub, kernel)

    first = await service.admit_device_pairing(setup_id="setup-1", payload=_request())
    second = await service.admit_device_pairing(setup_id="setup-1", payload=_request())

    assert first.outcome == second.outcome == "retry_required"
    assert first.completed_stage == second.completed_stage == "hub_approved"
    assert hub.calls[0]["request_id"] == hub.calls[1]["request_id"]
    assert hub.calls[0]["request_id"].endswith(":hub-pairing-claim")
    assert len(hub.calls[0]["request_id"]) <= 96


async def test_pairing_admission_refuses_to_run_without_internal_credential_source() -> None:
    hub, kernel = _Hub(), _Kernel()
    service = ControlPlaneService(
        directory=object(),  # type: ignore[arg-type]
        data=object(),  # type: ignore[arg-type]
        workspace=object(),  # type: ignore[arg-type]
        hub=hub,  # type: ignore[arg-type]
        kernel=kernel,  # type: ignore[arg-type]
    )

    with pytest.raises(AuthorityFailure) as caught:
        await service.admit_device_pairing(setup_id="setup-1", payload=_request())

    assert caught.value.kind == "configuration"
    assert hub.calls == []
    assert kernel.mount_calls == []
