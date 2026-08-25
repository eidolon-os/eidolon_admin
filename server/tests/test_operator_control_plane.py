"""Admin Web orchestration over the current Hub and Kernel contracts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from eidolon_sdk.device_foundation.v1 import (
    BusinessOwnerId,
    ControllerActorRef,
    DecideEnrollment,
    DeviceRef,
    EnrollmentProposalPage,
    EnrollmentProposalState,
    EnrollmentRecoveryProjection,
    ManifestRef,
    OwnerDomainId,
)

from eidolon_admin_server.app.control_plane.contracts import (
    KernelMount,
    KernelMountPage,
    KernelMutationResult,
    OperatorDeviceAdmissionRequest,
)
from eidolon_admin_server.app.control_plane.errors import AuthorityFailure
from eidolon_admin_server.app.control_plane.service import ControlPlaneService
from eidolon_admin_server.app.control_plane.router import operator_router

pytestmark = [pytest.mark.asyncio, pytest.mark.component]

OWNER_DOMAIN = OwnerDomainId("owner-domain-1")
BUSINESS_OWNER = BusinessOwnerId("owner_account_1")
MANIFEST = ManifestRef(
    manifest_id="manifest-1",
    revision=1,
    digest="sha256:" + "a" * 64,
)


async def test_operator_routes_are_separate_from_the_internal_service_plane() -> None:
    paths = {route.path for route in operator_router.routes}
    assert paths == {
        "/operator/v1/control-plane/owners/{owner_id}/inventory",
        "/operator/v1/control-plane/device-admissions/{device_id}",
    }


def recovery(state: EnrollmentProposalState) -> EnrollmentRecoveryProjection:
    now = datetime.now(UTC)
    approval = None
    if state != EnrollmentProposalState.PENDING_REVIEW:
        approval = {
            "decision_id": "decision-1",
            "enrollment_id": "enrollment-1",
            "decision": "approve",
            "actor": ControllerActorRef(
                principal_id="operator-1",
                owner_domain_id=OWNER_DOMAIN,
                granted_scopes=("device.read", "device.claim.approve"),
                authentication_strength="software",
            ),
            "target_owner_domain_id": OWNER_DOMAIN,
            "target_business_owner_id": BUSINESS_OWNER,
            "reviewed_manifest_ref": MANIFEST,
            "expected_proposal_revision": 1,
            "decided_at": now,
        }
    return EnrollmentRecoveryProjection.model_validate(
        {
            "proposal": {
                "enrollment_id": "enrollment-1",
                "proposal_revision": 1,
                "state": state,
                "device_instance_candidate_id": "device-1",
                "requested_owner_domain_id": OWNER_DOMAIN,
                "hardware_evidence_digest": "sha256:" + "b" * 64,
                "manifest_ref": MANIFEST,
                "handoff_key_id": "sha256:" + "c" * 64,
                "created_at": now,
                "expires_at": now + timedelta(minutes=10),
            },
            "approval_decision": approval,
            "grant_delivery": None,
            "claim": None,
            "source_revision": 1,
            "observed_at": now,
        }
    )


def page(item: EnrollmentRecoveryProjection) -> EnrollmentProposalPage:
    return EnrollmentProposalPage(
        owner_domain_id=OWNER_DOMAIN,
        items=(item,),
        next_cursor=None,
        observed_at=datetime.now(UTC),
    )


def mount(*, companion_id: str | None = None, revision: int = 1) -> KernelMount:
    now = datetime.now(UTC)
    return KernelMount(
        operation="kernel.device-mount",
        device_id="device-1",
        owner_id=str(BUSINESS_OWNER),
        device_ref=DeviceRef(
            device_instance_id="device-1",
            owner_domain_id=OWNER_DOMAIN,
            owner_domain_generation=1,
            claim_generation=1,
            trust_epoch=1,
        ),
        attached_companion_id=companion_id,
        revision=revision,
        created_at=now,
        updated_at=now,
        request_id="mount-1",
        fingerprint="sha256:" + "d" * 64,
        active=True,
    )


class Hub:
    def __init__(self, initial, after=None) -> None:
        self.initial = initial
        self.after = after or initial
        self.decision: DecideEnrollment | None = None

    async def list_authorized_enrollments(self, **_kwargs):
        return page(self.initial)

    async def decide_enrollment(self, *, command, **_kwargs):
        self.decision = command
        return type(
            "DecisionResult",
            (),
            {"proposal_revision": command.expected_proposal_revision},
        )()

    async def get_enrollment_recovery(self, **_kwargs):
        return self.after


class WaitingKernel:
    async def list_mounts(self, **_kwargs):
        return KernelMountPage(
            operation="kernel.device-mount-page", next_cursor=None, mounts=()
        )

    async def mount(self, **_kwargs):
        raise AuthorityFailure("kernel", "not_found", "claim not visible", 404)


class MountedKernel:
    def __init__(self) -> None:
        self.current = mount()

    async def list_mounts(self, **_kwargs):
        return KernelMountPage(
            operation="kernel.device-mount-page",
            next_cursor=None,
            mounts=(self.current,),
        )

    async def attach(self, *, companion_id: str, **_kwargs):
        self.current = mount(companion_id=companion_id, revision=2)
        return KernelMutationResult(
            operation="kernel.device-mount-mutation-result",
            mount=self.current,
            audit_position=2,
            replayed=False,
        )


def service(hub, kernel) -> ControlPlaneService:
    unused = object()
    return ControlPlaneService(
        directory=unused,  # type: ignore[arg-type]
        data=unused,  # type: ignore[arg-type]
        workspace=unused,  # type: ignore[arg-type]
        hub=hub,
        kernel=kernel,
        memory=unused,  # type: ignore[arg-type]
        activity=unused,  # type: ignore[arg-type]
    )


def request(*, companion_id: str | None = None) -> OperatorDeviceAdmissionRequest:
    return OperatorDeviceAdmissionRequest(
        request_id="operator-1",
        owner_id=BUSINESS_OWNER,
        device_id="device-1",
        companion_id=companion_id,
        expected_mount_revision=0,
        replace_existing_mount=False,
    )


async def test_pending_enrollment_is_decided_then_waits_for_claim_ack() -> None:
    hub = Hub(
        recovery(EnrollmentProposalState.PENDING_REVIEW),
        recovery(EnrollmentProposalState.APPROVED_AWAITING_HANDOFF),
    )
    result = await service(hub, WaitingKernel()).admit_operator_device(
        request(), hub_authorization="Bearer operator-jwt"
    )

    assert hub.decision is not None
    assert hub.decision.target_business_owner_id == BUSINESS_OWNER
    assert result.outcome == "retry_required"
    assert result.completed_stage == "hub_approved"
    assert [step.name for step in result.steps] == [
        "hub_approval",
        "kernel_mount",
        "companion_attachment",
    ]
    assert result.steps[1].failure is not None
    assert result.steps[1].failure.retryable is True


async def test_existing_approval_and_mount_continue_to_companion_attachment() -> None:
    hub = Hub(recovery(EnrollmentProposalState.APPROVED_AWAITING_HANDOFF))
    result = await service(hub, MountedKernel()).admit_operator_device(
        request(companion_id="companion-1"),
        hub_authorization="Bearer operator-jwt",
    )

    assert hub.decision is None
    assert result.outcome == "completed"
    assert result.completed_stage == "companion_attached"
    assert result.mount is not None
    assert result.mount.attached_companion_id == "companion-1"
    assert [step.state for step in result.steps] == [
        "replayed",
        "replayed",
        "committed",
    ]
