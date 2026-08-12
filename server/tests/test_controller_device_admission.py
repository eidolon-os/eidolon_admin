from __future__ import annotations

from datetime import UTC, datetime

import jwt
import pytest

from eidolon_admin_server.app.control_plane.contracts import (
    ControllerDeviceAdmissionRequest,
    ControllerDeviceRemovalRequest,
    HubDevicePage,
    HubLifecycleStatus,
    KernelMount,
    KernelMountPage,
    KernelMutationResult,
)
from eidolon_admin_server.app.control_plane.errors import AuthorityFailure
from eidolon_admin_server.app.control_plane.hub_credentials import (
    HubAdminCredentialIssuer,
)
from eidolon_admin_server.app.control_plane.service import ControlPlaneService

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]

_SECRET = b"admin-hub-owner-management-secret-32-bytes"


class _Hub:
    calls: list[dict]

    def __init__(self) -> None:
        self.calls = []
        self.revoke_failure: AuthorityFailure | None = None

    async def approve(self, **kwargs) -> HubLifecycleStatus:
        self.calls.append(kwargs)
        return HubLifecycleStatus(
            operation="device.lifecycle-status",
            device_id=kwargs["device_id"],
            owner_id=kwargs["owner_id"],
            lifecycle_state="approved",
        )

    async def revoke(self, **kwargs) -> HubLifecycleStatus:
        self.calls.append(kwargs)
        if self.revoke_failure:
            raise self.revoke_failure
        return HubLifecycleStatus(
            operation="device.lifecycle-status",
            device_id=kwargs["device_id"],
            owner_id="owner-1",
            lifecycle_state="revoked",
        )

    async def list_devices(self, **kwargs) -> HubDevicePage:
        self.calls.append(kwargs)
        return HubDevicePage.model_validate(
            {
                "operation": "device.directory-page",
                "next_cursor": None,
                "devices": [],
            }
        )


class _Kernel:
    def __init__(self) -> None:
        self.mount_calls: list[dict] = []
        self.attach_calls: list[dict] = []
        self.unmount_calls: list[dict] = []
        self.mount_failure: AuthorityFailure | None = None
        self.mounted: tuple[KernelMount, ...] = ()

    async def list_mounts(self, **_kwargs) -> KernelMountPage:
        return KernelMountPage(
            operation="kernel.device-mount-page",
            next_cursor=None,
            mounts=self.mounted,
        )

    async def unmount(self, **kwargs) -> KernelMutationResult:
        self.unmount_calls.append(kwargs)
        mount = _mount(
            device_id=kwargs["device_id"],
            owner_id=kwargs["owner_id"],
            request_id=kwargs["request_id"],
            revision=kwargs["expected_revision"] + 1,
        )
        return KernelMutationResult(
            operation="kernel.device-mount-mutation-result",
            mount=mount.model_copy(update={"active": False}),
            audit_position=3,
            replayed=False,
        )

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


def _request() -> ControllerDeviceAdmissionRequest:
    return ControllerDeviceAdmissionRequest(
        contract_version="1",
        request_id="mobile-claim-1",
        owner_id="owner-1",
        controller_id="ectrl-0123456789abcdefabcd",
        device_id="device-1",
        companion_id="companion-1",
    )


def _service(hub: _Hub, kernel: _Kernel) -> ControlPlaneService:
    return ControlPlaneService(
        directory=object(),  # type: ignore[arg-type]
        data=object(),  # type: ignore[arg-type]
        workspace=object(),  # type: ignore[arg-type]
        hub=hub,  # type: ignore[arg-type]
        kernel=kernel,  # type: ignore[arg-type]
        hub_credentials=HubAdminCredentialIssuer(secret=_SECRET),
    )


async def test_controller_admission_mints_admin_credential_and_binds_device() -> None:
    hub, kernel = _Hub(), _Kernel()

    result = await _service(hub, kernel).admit_controller_device(payload=_request())

    assert result.outcome == "completed"
    assert result.completed_stage == "companion_attached"
    assert result.hub is not None
    assert result.hub.device_id == "device-1"
    assert kernel.mount_calls[0]["device_id"] == "device-1"
    assert kernel.attach_calls[0]["device_id"] == "device-1"
    encoded = hub.calls[0]["authorization"].removeprefix("Bearer ")
    claims = jwt.decode(encoded, _SECRET, algorithms=["HS256"], audience="eidolon-hub")
    assert "owner_id" not in claims
    assert claims["roles"] == ["hub-admin"]
    assert claims["sub"] == "eidolon-local-api/ectrl-0123456789abcdefabcd"


async def test_controller_admission_retry_reuses_all_deterministic_child_ids() -> None:
    hub, kernel = _Hub(), _Kernel()
    kernel.mount_failure = AuthorityFailure(
        "kernel", "unavailable", "kernel down", 503, retryable=True
    )
    service = _service(hub, kernel)

    first = await service.admit_controller_device(payload=_request())
    second = await service.admit_controller_device(payload=_request())

    assert first.outcome == second.outcome == "retry_required"
    assert first.completed_stage == second.completed_stage == "hub_approved"
    assert hub.calls[0]["request_id"] == hub.calls[1]["request_id"]
    assert hub.calls[0]["request_id"].endswith(":hub-approve")
    assert len(hub.calls[0]["request_id"]) <= 96


async def test_controller_admission_refuses_missing_internal_credential_source() -> None:
    hub, kernel = _Hub(), _Kernel()
    service = ControlPlaneService(
        directory=object(),  # type: ignore[arg-type]
        data=object(),  # type: ignore[arg-type]
        workspace=object(),  # type: ignore[arg-type]
        hub=hub,  # type: ignore[arg-type]
        kernel=kernel,  # type: ignore[arg-type]
    )

    with pytest.raises(AuthorityFailure) as caught:
        await service.admit_controller_device(payload=_request())

    assert caught.value.kind == "configuration"
    assert hub.calls == []
    assert kernel.mount_calls == []


async def test_pending_directory_uses_admin_credential_and_unclaimed_scope() -> None:
    hub, kernel = _Hub(), _Kernel()

    result = await _service(hub, kernel).list_pending_device_enrollments(
        controller_id="ectrl-0123456789abcdefabcd"
    )

    assert result.devices == ()
    assert hub.calls[0]["owner_id"] == "unclaimed"
    encoded = hub.calls[0]["authorization"].removeprefix("Bearer ")
    claims = jwt.decode(encoded, _SECRET, algorithms=["HS256"], audience="eidolon-hub")
    assert claims["roles"] == ["hub-admin"]
    assert "owner_id" not in claims


def _removal_request() -> ControllerDeviceRemovalRequest:
    return ControllerDeviceRemovalRequest(
        contract_version="1",
        request_id="mobile-remove-1",
        owner_id="owner-1",
        controller_id="ectrl-0123456789abcdefabcd",
        device_id="device-1",
    )


async def test_removal_revokes_the_grant_then_drops_the_mount() -> None:
    hub, kernel = _Hub(), _Kernel()
    kernel.mounted = (
        _mount(device_id="device-1", owner_id="owner-1", request_id="r", revision=3),
    )

    result = await _service(hub, kernel).remove_controller_device(payload=_removal_request())

    assert result.outcome == "completed"
    assert result.completed_stage == "kernel_unmounted"
    assert [step.name for step in result.steps] == ["hub_revocation", "kernel_unmount"]
    assert hub.calls[0]["reason"] == "owner-removed"
    # The mount is dropped at the revision that was read, so a mount that moved
    # underneath fails the compare instead of removing the wrong state.
    assert kernel.unmount_calls[0]["expected_revision"] == 3


async def test_removal_is_idempotent_when_nothing_is_mounted() -> None:
    hub, kernel = _Hub(), _Kernel()

    result = await _service(hub, kernel).remove_controller_device(payload=_removal_request())

    assert result.outcome == "completed"
    assert kernel.unmount_calls == []
    assert result.steps[-1].state == "not_requested"


async def test_a_hub_that_refuses_leaves_the_mount_alone() -> None:
    hub, kernel = _Hub(), _Kernel()
    hub.revoke_failure = AuthorityFailure("hub", "not_found", "no such device", 404)
    kernel.mounted = (
        _mount(device_id="device-1", owner_id="owner-1", request_id="r", revision=3),
    )

    result = await _service(hub, kernel).remove_controller_device(payload=_removal_request())

    assert result.outcome == "blocked"
    assert result.completed_stage == "received"
    assert kernel.unmount_calls == []
    assert result.steps[0].failure is not None


async def test_repeating_a_removal_reuses_the_same_child_request_ids() -> None:
    hub, kernel = _Hub(), _Kernel()
    kernel.mounted = (
        _mount(device_id="device-1", owner_id="owner-1", request_id="r", revision=3),
    )
    service = _service(hub, kernel)

    first = await service.remove_controller_device(payload=_removal_request())
    second = await service.remove_controller_device(payload=_removal_request())

    assert first.steps[0].request_id == second.steps[0].request_id
    assert kernel.unmount_calls[0]["request_id"] == kernel.unmount_calls[1]["request_id"]


async def test_re_admitting_a_removed_device_mounts_at_its_current_revision() -> None:
    # Removal leaves the mount record behind, inactive. Adding the device back
    # has to compare against that revision — mounting at 0 would be rejected,
    # which is exactly what stranded a phone the owner had just removed.
    hub, kernel = _Hub(), _Kernel()
    kernel.mounted = (
        _mount(
            device_id="device-1",
            owner_id="owner-1",
            request_id="r",
            revision=3,
        ).model_copy(update={"active": False}),
    )

    await _service(hub, kernel).admit_controller_device(payload=_request())

    assert kernel.mount_calls[0]["expected_revision"] == 3
    assert kernel.mount_calls[0]["replace_existing"] is False


async def test_a_first_admission_still_mounts_from_nothing() -> None:
    hub, kernel = _Hub(), _Kernel()

    await _service(hub, kernel).admit_controller_device(payload=_request())

    assert kernel.mount_calls[0]["expected_revision"] == 0
