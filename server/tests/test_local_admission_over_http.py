"""The Local API and the control plane, talking to each other over real HTTP.

Everything between them is a canonical DTO serialized to JSON and validated
back. Both sides import the same models, which is exactly why nothing here was
covered: a stubbed transport never encodes anything, and a shared model looks
like it cannot disagree with itself. It can — a model that parses in JSON mode
and not in Python mode is a response body that cannot be a request body — and
the first place that showed was a Host answering 422 to itself.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
from eidolon_sdk.device_foundation.v1 import (
    ApprovalDecision,
    ClaimPage,
    ClaimQuery,
    ClaimState,
    DecideEnrollmentResult,
    EnrollmentProposal,
    EnrollmentProposalPage,
    EnrollmentProposalQuery,
    EnrollmentProposalState,
    EnrollmentRecoveryProjection,
    ManifestRef,
    OwnerDomainId,
    BusinessOwnerId,
)
from fastapi import FastAPI

from eidolon_admin_server.app.control_plane.contracts import (
    AdmissionDecisionWorkflowResult,
)
from eidolon_admin_server.app.control_plane.router import router as control_plane_router
from eidolon_admin_server.app.service_auth import require_local_api_credential
from eidolon_admin_server.local_api.device_admissions import (
    AdminDeviceAdmissionClient,
    claim_query,
    enrollment_query,
    enrollment_recovery_query,
    LocalEnrollmentDecisionRequest,
)

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 8, 25, tzinfo=UTC)
DOMAIN = OwnerDomainId("owner-b0a862b0aab941d64554")
BUSINESS_OWNER = BusinessOwnerId("owner_683f0000000000000000")
CONTROLLER = "ectrl-9c9b71c830d92e0c875b"
ENROLLMENT = "enrollment_eIlo875sU_qWaFHrdbHYC3t9"
MANIFEST = ManifestRef(
    manifest_id="box3-device-manifest", revision=1, digest="sha256:" + "a" * 64
)


def _proposal(state: EnrollmentProposalState) -> EnrollmentProposal:
    return EnrollmentProposal(
        enrollment_id=ENROLLMENT,
        proposal_revision=1,
        state=state,
        device_instance_candidate_id="device-instance-" + "c" * 64,
        requested_owner_domain_id=DOMAIN,
        hardware_evidence_digest="sha256:" + "b" * 64,
        manifest_ref=MANIFEST,
        handoff_key_id="sha256:" + "d" * 64,
        created_at=NOW,
        expires_at=datetime(2026, 8, 25, 0, 15, tzinfo=UTC),
    )


def _projection(
    state: EnrollmentProposalState, decision=None
) -> EnrollmentRecoveryProjection:
    return EnrollmentRecoveryProjection(
        proposal=_proposal(state),
        approval_decision=decision,
        grant_delivery=None,
        claim=None,
        source_revision=1,
        observed_at=NOW,
    )


class _Service:
    """The control plane's answers, as canonical objects."""

    def __init__(self) -> None:
        self.seen: list[object] = []

    async def list_enrollment_recovery(self, *, payload) -> EnrollmentProposalPage:
        self.seen.append(payload)
        return EnrollmentProposalPage(
            owner_domain_id=DOMAIN,
            items=(_projection(EnrollmentProposalState.PENDING_REVIEW),),
            next_cursor=None,
            observed_at=NOW,
        )

    async def get_enrollment_recovery(self, *, payload) -> EnrollmentRecoveryProjection:
        self.seen.append(payload)
        return _projection(EnrollmentProposalState.PENDING_REVIEW)

    async def list_claims(self, *, payload) -> ClaimPage:
        self.seen.append(payload)
        return ClaimPage(
            owner_domain_id=DOMAIN, items=(), next_cursor=None, observed_at=NOW
        )

    async def decide_controller_enrollment(
        self, *, payload
    ) -> AdmissionDecisionWorkflowResult:
        self.seen.append(payload)
        decision = ApprovalDecision(
            decision_id="decision_01",
            enrollment_id=ENROLLMENT,
            decision="approve",
            actor=payload.actor,
            target_owner_domain_id=DOMAIN,
            target_business_owner_id=BUSINESS_OWNER,
            reviewed_manifest_ref=MANIFEST,
            expected_proposal_revision=1,
            decided_at=NOW,
        )
        return AdmissionDecisionWorkflowResult(
            request_id=payload.request_id,
            intent_id="admission-intent-" + "a" * 32,
            command_id="decide-enrollment-" + "b" * 32,
            checkpoint="decision_committed",
            decision_result=DecideEnrollmentResult(
                decision_id="decision_01",
                decision="approve",
                decided_by=payload.actor,
                decided_at=NOW,
                proposal_revision=1,
            ),
            recovery=_projection(
                EnrollmentProposalState.APPROVED_AWAITING_HANDOFF, decision
            ),
        )


def _client(service: _Service) -> AdminDeviceAdmissionClient:
    app = FastAPI()
    app.state.control_plane = service
    app.dependency_overrides[require_local_api_credential] = lambda: None
    app.include_router(control_plane_router, prefix="/api")
    transport = httpx.ASGITransport(app=app)
    return AdminDeviceAdmissionClient(
        base_url="http://admin.test",
        service_token="local-api-service-token",
        timeout_seconds=5,
        client=httpx.AsyncClient(transport=transport, base_url="http://admin.test"),
    )


async def test_the_pending_queue_survives_being_json() -> None:
    service = _Service()
    admission = _client(service)
    try:
        page = await admission.query_enrollments(
            payload=enrollment_query(
                controller_id=CONTROLLER,
                owner_domain_id=DOMAIN,
                business_owner_id=BUSINESS_OWNER,
                query=EnrollmentProposalQuery(
                    owner_domain_id=DOMAIN,
                    states=(
                        EnrollmentProposalState.PENDING_REVIEW,
                        EnrollmentProposalState.GRANT_ACKNOWLEDGED,
                    ),
                    cursor=None,
                    limit=50,
                ),
            )
        )
    finally:
        await admission.close()

    assert [item.proposal.enrollment_id for item in page.items] == [ENROLLMENT]
    # The states arrived as the enum they were sent as, not as strings that
    # happened to compare equal.
    received = service.seen[0].query.states
    assert received == (
        EnrollmentProposalState.PENDING_REVIEW,
        EnrollmentProposalState.GRANT_ACKNOWLEDGED,
    )


async def test_one_enrollment_and_the_claim_page_survive_being_json() -> None:
    service = _Service()
    admission = _client(service)
    try:
        projection = await admission.recover_enrollment(
            payload=enrollment_recovery_query(
                controller_id=CONTROLLER,
                owner_domain_id=DOMAIN,
                business_owner_id=BUSINESS_OWNER,
                enrollment_id=ENROLLMENT,
            )
        )
        claims = await admission.query_claims(
            payload=claim_query(
                controller_id=CONTROLLER,
                owner_domain_id=DOMAIN,
                business_owner_id=BUSINESS_OWNER,
                query=ClaimQuery(
                    owner_domain_id=DOMAIN,
                    states=(ClaimState.ACTIVE, ClaimState.REVOKED),
                    cursor=None,
                    limit=50,
                ),
            )
        )
    finally:
        await admission.close()

    assert projection.proposal.enrollment_id == ENROLLMENT
    assert projection.proposal.state is EnrollmentProposalState.PENDING_REVIEW
    assert claims.owner_domain_id == DOMAIN
    assert service.seen[1].query.states == (ClaimState.ACTIVE, ClaimState.REVOKED)


async def test_the_decision_survives_being_json() -> None:
    service = _Service()
    admission = _client(service)
    request = LocalEnrollmentDecisionRequest(
        contract_version="1",
        request_id="mobile-decision-setup-01",
        expected_proposal_revision=1,
        decision="approve",
        reviewed_manifest_ref=MANIFEST,
        expected_owner_domain_id=str(DOMAIN),
        expected_business_owner_id=str(BUSINESS_OWNER),
        initial_assignment_intent={"companion_id": "c_01"},
    )
    try:
        result = await admission.decide(
            payload=request.to_admin(
                enrollment_id=ENROLLMENT,
                owner_domain_id=DOMAIN,
                business_owner_id=BUSINESS_OWNER,
                controller_id=CONTROLLER,
            )
        )
    finally:
        await admission.close()

    assert result.checkpoint == "decision_committed"
    assert result.recovery.approval_decision is not None
    assert result.recovery.proposal.state is (
        EnrollmentProposalState.APPROVED_AWAITING_HANDOFF
    )
    assert service.seen[0].decision.initial_assignment_intent == {
        "companion_id": "c_01"
    }
