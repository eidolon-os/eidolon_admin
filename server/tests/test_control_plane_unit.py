"""Pure application tests for the Admin-owned control-plane workflow."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from eidolon_admin_server.app.control_plane.contracts import (
    DeviceAdmissionRequest,
    HubDevicePage,
    HubLifecycleStatus,
    KernelMount,
    KernelMountPage,
    KernelMutationResult,
)
from eidolon_admin_server.app.control_plane.errors import AuthorityFailure
from eidolon_admin_server.app.control_plane.service import ControlPlaneService

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


def mount(*, revision: int = 1, replayed_request: str = "request") -> KernelMount:
    now = datetime.now(UTC)
    return KernelMount(
        operation="kernel.device-mount",
        device_id="device-1",
        owner_id="owner-1",
        attached_companion_id=None,
        revision=revision,
        created_at=now,
        updated_at=now,
        request_id=replayed_request,
        fingerprint="sha256:" + "a" * 64,
        active=True,
    )


class FakeDirectory:
    closed = False

    async def close(self) -> None:
        self.closed = True


class FakeData:
    pass


class FakeHub:
    def __init__(self) -> None:
        self.approval_ids: list[str] = []
        self.fail: AuthorityFailure | None = None
        self.list_started: asyncio.Event | None = None
        self.list_release: asyncio.Event | None = None

    async def approve(self, **kwargs) -> HubLifecycleStatus:
        if self.fail:
            raise self.fail
        self.approval_ids.append(kwargs["request_id"])
        return HubLifecycleStatus(
            operation="device.lifecycle-status",
            device_id=kwargs["device_id"],
            owner_id=kwargs["owner_id"],
            lifecycle_state="approved",
        )

    async def list_devices(self, **_kwargs) -> HubDevicePage:
        if self.list_started:
            self.list_started.set()
        if self.list_release:
            await self.list_release.wait()
        return HubDevicePage(
            operation="device.directory-page", next_cursor=None, devices=()
        )


class FakeKernel:
    def __init__(self) -> None:
        self.mount_ids: list[str] = []
        self.attach_ids: list[str] = []
        self.mount_failure: AuthorityFailure | None = None
        self.attach_failure: AuthorityFailure | None = None
        self.list_observed_hub = False
        self.hub_started: asyncio.Event | None = None
        self.hub_release: asyncio.Event | None = None

    async def mount(self, **kwargs) -> KernelMutationResult:
        if self.mount_failure:
            raise self.mount_failure
        request_id = kwargs["request_id"]
        replayed = request_id in self.mount_ids
        self.mount_ids.append(request_id)
        return KernelMutationResult(
            operation="kernel.device-mount-mutation-result",
            mount=mount(revision=1, replayed_request=request_id),
            audit_position=1,
            replayed=replayed,
        )

    async def attach(self, **kwargs) -> KernelMutationResult:
        if self.attach_failure:
            raise self.attach_failure
        request_id = kwargs["request_id"]
        replayed = request_id in self.attach_ids
        self.attach_ids.append(request_id)
        attached = mount(revision=2, replayed_request=request_id).model_copy(
            update={"attached_companion_id": kwargs["companion_id"]}
        )
        return KernelMutationResult(
            operation="kernel.device-mount-mutation-result",
            mount=attached,
            audit_position=2,
            replayed=replayed,
        )

    async def list_mounts(self, **_kwargs) -> KernelMountPage:
        if self.hub_started:
            await self.hub_started.wait()
            self.list_observed_hub = True
        if self.hub_release:
            self.hub_release.set()
        return KernelMountPage(operation="kernel.device-mount-page", mounts=())


def service(hub: FakeHub, kernel: FakeKernel) -> ControlPlaneService:
    return ControlPlaneService(
        directory=FakeDirectory(),  # type: ignore[arg-type]
        data=FakeData(),  # type: ignore[arg-type]
        hub=hub,  # type: ignore[arg-type]
        kernel=kernel,  # type: ignore[arg-type]
    )


def request(*, companion_id: str | None = None) -> DeviceAdmissionRequest:
    return DeviceAdmissionRequest(
        request_id="workflow-1",
        owner_id="owner-1",
        device_id="device-1",
        companion_id=companion_id,
    )


async def test_successful_workflow_uses_deterministic_child_ids() -> None:
    hub, kernel = FakeHub(), FakeKernel()
    result = await service(hub, kernel).admit_device(
        request(companion_id="companion-1"), hub_authorization="Bearer operator"
    )

    assert result.outcome == "completed"
    assert result.completed_stage == "companion_attached"
    assert result.distributed_atomic is False
    assert result.compensation == "none-safe-intermediate"
    assert hub.approval_ids == ["admin:workflow-1:hub-approve"]
    assert kernel.mount_ids == ["admin:workflow-1:kernel-mount"]
    assert kernel.attach_ids == ["admin:workflow-1:kernel-attach"]


async def test_mount_failure_reports_hub_safe_intermediate() -> None:
    hub, kernel = FakeHub(), FakeKernel()
    kernel.mount_failure = AuthorityFailure(
        "kernel", "unavailable", "kernel down", 503, retryable=True
    )

    result = await service(hub, kernel).admit_device(
        request(companion_id="companion-1"), hub_authorization="Bearer operator"
    )

    assert result.outcome == "retry_required"
    assert result.completed_stage == "hub_approved"
    assert result.recovery == "retry-forward-same-request-id"
    assert [step.state for step in result.steps] == [
        "committed",
        "failed",
        "not_attempted",
    ]
    assert result.steps[1].failure is not None
    assert result.steps[1].failure.retryable is True


async def test_attachment_failure_reports_mounted_safe_intermediate() -> None:
    hub, kernel = FakeHub(), FakeKernel()
    kernel.attach_failure = AuthorityFailure(
        "kernel", "upstream_failure", "Data companion authority down", 502, 503, True
    )

    result = await service(hub, kernel).admit_device(
        request(companion_id="companion-1"), hub_authorization="Bearer operator"
    )

    assert result.outcome == "retry_required"
    assert result.completed_stage == "kernel_mounted"
    assert result.mount is not None
    assert result.steps[-1].failure is not None
    assert result.steps[-1].failure.authority == "kernel"
    assert result.steps[-1].failure.upstream_status == 503


async def test_forward_recovery_failure_does_not_regress_committed_hub_state() -> None:
    hub, kernel = FakeHub(), FakeKernel()
    kernel.mount_failure = AuthorityFailure(
        "kernel", "upstream_failure", "write still unavailable", 502, 503, True
    )
    subject = service(hub, kernel)

    first = await subject.admit_device(request(), hub_authorization="Bearer operator")
    retry = await subject.admit_device(request(), hub_authorization="Bearer operator")

    assert first.completed_stage == retry.completed_stage == "hub_approved"
    assert retry.outcome == "retry_required"
    assert retry.recovery == "retry-forward-same-request-id"
    assert hub.approval_ids == [
        "admin:workflow-1:hub-approve",
        "admin:workflow-1:hub-approve",
    ]
    assert kernel.mount_ids == []


async def test_non_retryable_cas_conflict_requires_operator_action() -> None:
    hub, kernel = FakeHub(), FakeKernel()
    kernel.mount_failure = AuthorityFailure(
        "kernel", "conflict", "request_id payload mismatch", 409, 409, False
    )

    result = await service(hub, kernel).admit_device(
        request(), hub_authorization="Bearer operator"
    )

    assert result.outcome == "blocked"
    assert result.completed_stage == "hub_approved"
    assert result.recovery == "operator-action-required"
    assert result.steps[1].failure is not None
    assert result.steps[1].failure.kind == "conflict"


async def test_hub_failure_does_not_attempt_kernel() -> None:
    hub, kernel = FakeHub(), FakeKernel()
    hub.fail = AuthorityFailure("hub", "forbidden", "denied", 403)

    with pytest.raises(AuthorityFailure, match="denied"):
        await service(hub, kernel).admit_device(
            request(), hub_authorization="Bearer invalid"
        )
    assert kernel.mount_ids == []


async def test_repeated_request_reuses_ids_and_surfaces_replay() -> None:
    hub, kernel = FakeHub(), FakeKernel()
    subject = service(hub, kernel)

    first = await subject.admit_device(request(), hub_authorization="Bearer operator")
    second = await subject.admit_device(request(), hub_authorization="Bearer operator")

    assert first.outcome == second.outcome == "completed"
    assert hub.approval_ids == [
        "admin:workflow-1:hub-approve",
        "admin:workflow-1:hub-approve",
    ]
    assert second.steps[1].state == "replayed"


async def test_inventory_starts_hub_and_kernel_reads_concurrently() -> None:
    hub, kernel = FakeHub(), FakeKernel()
    started, release = asyncio.Event(), asyncio.Event()
    hub.list_started = started
    hub.list_release = release
    kernel.hub_started = started
    kernel.hub_release = release

    result = await asyncio.wait_for(
        service(hub, kernel).inventory(
            owner_id="owner-1", hub_authorization="Bearer operator"
        ),
        timeout=1,
    )

    assert kernel.list_observed_hub is True
    assert result.degraded is False


async def test_inventory_preserves_hub_auth_failure() -> None:
    class UnauthorizedHub(FakeHub):
        async def list_devices(self, **_kwargs):
            raise AuthorityFailure("hub", "unauthorized", "bad credential", 401)

    with pytest.raises(AuthorityFailure) as caught:
        await service(UnauthorizedHub(), FakeKernel()).inventory(
            owner_id="owner-1", hub_authorization="Bearer bad"
        )
    assert caught.value.kind == "unauthorized"
    assert caught.value.status_code == 401


async def test_close_releases_owned_directory_client() -> None:
    directory = FakeDirectory()
    subject = ControlPlaneService(
        directory=directory,  # type: ignore[arg-type]
        data=FakeData(),  # type: ignore[arg-type]
        hub=FakeHub(),  # type: ignore[arg-type]
        kernel=FakeKernel(),  # type: ignore[arg-type]
    )
    await subject.close()
    assert directory.closed is True
