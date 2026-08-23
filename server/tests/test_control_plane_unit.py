"""Pure application tests for the Admin-owned control-plane workflow."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from eidolon_admin_server.app.control_plane.contracts import (
    DeviceAdmissionRequest,
    DeviceRef,
    HubDeviceEvent,
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


def mount(*, revision: int = 1, replayed_request: str = "request") -> KernelMount:
    now = datetime.now(UTC)
    return KernelMount(
        operation="kernel.device-mount",
        device_id="device-1",
        owner_id="owner-1",
        device_ref=DeviceRef(
            device_instance_id="device-1",
            owner_domain_id="owner-1",
            claim_generation=1,
            trust_epoch=1,
            accepted_manifest_digest="sha256:" + "a" * 64,
        ),
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


class FakeWorkspace:
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
        workspace=FakeWorkspace(),  # type: ignore[arg-type]
        hub=hub,  # type: ignore[arg-type]
        kernel=kernel,  # type: ignore[arg-type]
        memory=object(),  # type: ignore[arg-type]
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
        workspace=FakeWorkspace(),  # type: ignore[arg-type]
        hub=FakeHub(),  # type: ignore[arg-type]
        kernel=FakeKernel(),  # type: ignore[arg-type]
        memory=object(),  # type: ignore[arg-type]
    )
    await subject.close()
    assert directory.closed is True


def _history_hub() -> "HistoryHub":
    return HistoryHub()


class HistoryHub(FakeHub):
    """A Hub holding the two scopes a device's life is split across."""

    def __init__(self) -> None:
        super().__init__()
        self.failing_scope: str | None = None
        self.events: dict[str, list[HubDeviceEvent]] = {
            "owner-1": [
                self.event(
                    2,
                    "eidolon.device.approved.v1",
                    "eidolon-local-api/ectrl-1",
                    minute=14,
                )
            ],
            "unclaimed": [
                self.event(
                    1,
                    "eidolon.device.enrolled.v1",
                    "untrusted-device:device-1",
                    minute=6,
                ),
                self.event(
                    2,
                    "eidolon.device.enrolled.v1",
                    "untrusted-device:device-2",
                    minute=20,
                    device_id="device-2",
                ),
            ],
        }

    @staticmethod
    def event(
        position: int,
        event_type: str,
        principal_id: str,
        *,
        minute: int,
        device_id: str = "device-1",
    ) -> HubDeviceEvent:
        return HubDeviceEvent(
            operation="device.management-event",
            stream_position=position,
            event_id=f"{event_type}-{position}-{device_id}",
            event_type=event_type,
            source="eidolon-hub/device-management",
            principal_id=principal_id,
            device_id=device_id,
            occurred_at=datetime(2026, 8, 17, 10, minute, tzinfo=UTC),
            data={},
        )

    @staticmethod
    def directory_entry(device_id: str, owner_scope: str, name: str) -> dict:
        now = datetime(2026, 8, 17, 10, 0, tzinfo=UTC)
        return {
            "operation": "device.directory-entry",
            "device_id": device_id,
            "owner_scope": owner_scope,
            "display_name": name,
            "device_kind": "esp-box-3",
            "manifest": {
                "schema_version": 1,
                "title": "Box-3",
                "properties": [],
                "actions": [],
                "events": [],
                "media": [],
            },
            "manifest_revision": "rev-1",
            "lifecycle_state": "approved",
            "enrolled_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }

    async def latest_events(self, **kwargs) -> tuple[HubDeviceEvent, ...]:
        owner_id = kwargs["owner_id"]
        if self.failing_scope == owner_id:
            raise AuthorityFailure("hub", "unavailable", "hub is unreachable", 503)
        return tuple(self.events[owner_id])

    async def list_devices(self, **kwargs) -> HubDevicePage:
        owner_id = kwargs["owner_id"]
        entries = {
            "owner-1": [self.directory_entry("device-1", "owner-1", "客厅的 Box-3")],
            "unclaimed": [
                self.directory_entry("device-2", "unclaimed", "门口的新板子")
            ],
        }[owner_id]
        return HubDevicePage.model_validate(
            {
                "operation": "device.directory-page",
                "next_cursor": None,
                "devices": entries,
            }
        )


def history_service(hub: HistoryHub) -> ControlPlaneService:
    return ControlPlaneService(
        directory=FakeDirectory(),  # type: ignore[arg-type]
        data=FakeData(),  # type: ignore[arg-type]
        workspace=FakeWorkspace(),  # type: ignore[arg-type]
        hub=hub,  # type: ignore[arg-type]
        kernel=FakeKernel(),  # type: ignore[arg-type]
        memory=object(),  # type: ignore[arg-type]
        hub_credentials=HubAdminCredentialIssuer(secret=b"x" * 32),
    )


async def test_device_history_joins_the_doorstep_to_the_owners_own_records() -> None:
    """A device knocks before anyone holds it, and both halves are the story."""

    history = await history_service(_history_hub()).local_owner_device_history(
        owner_id="owner-1",
        controller_id="ectrl-1",
        limit=10,
    )

    # Newest first, across both scopes; the stream position that orders one
    # scope says nothing about the other.
    assert [event.event_id for event in history.events] == [
        "eidolon.device.enrolled.v1-2-device-2",
        "eidolon.device.approved.v1-2-device-1",
        "eidolon.device.enrolled.v1-1-device-1",
    ]
    # Both directories travel, so a device that never got claimed is still
    # something a person can read the name of.
    assert {entry.device_id: entry.display_name for entry in history.devices} == {
        "device-1": "客厅的 Box-3",
        "device-2": "门口的新板子",
    }


async def test_device_history_keeps_only_what_was_asked_for() -> None:
    history = await history_service(_history_hub()).local_owner_device_history(
        owner_id="owner-1",
        controller_id="ectrl-1",
        limit=1,
    )

    assert len(history.events) == 1
    # The directory that travels is the one those events need, not the rest.
    assert [entry.device_id for entry in history.devices] == ["device-2"]


@pytest.mark.parametrize("scope", ["owner-1", "unclaimed"])
async def test_a_scope_that_cannot_be_read_is_not_a_quiet_stretch(scope: str) -> None:
    """Half a history looks exactly like nothing having happened."""

    hub = _history_hub()
    hub.failing_scope = scope

    with pytest.raises(AuthorityFailure) as caught:
        await history_service(hub).local_owner_device_history(
            owner_id="owner-1",
            controller_id="ectrl-1",
            limit=10,
        )
    assert caught.value.authority == "hub"
    assert caught.value.status_code == 503


async def test_device_history_without_a_hub_credential_says_so() -> None:
    subject = ControlPlaneService(
        directory=FakeDirectory(),  # type: ignore[arg-type]
        data=FakeData(),  # type: ignore[arg-type]
        workspace=FakeWorkspace(),  # type: ignore[arg-type]
        hub=_history_hub(),  # type: ignore[arg-type]
        kernel=FakeKernel(),  # type: ignore[arg-type]
        memory=object(),  # type: ignore[arg-type]
    )

    with pytest.raises(AuthorityFailure) as caught:
        await subject.local_owner_device_history(
            owner_id="owner-1",
            controller_id="ectrl-1",
            limit=10,
        )
    assert caught.value.kind == "configuration"
