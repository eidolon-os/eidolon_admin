from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from eidolon_sdk.device_foundation.v1 import (
    ApprovalDecision,
    BusinessOwnerId,
    ControllerActorRef,
    DecideEnrollment,
    DecideEnrollmentResult,
    EnrollmentProposal,
    EnrollmentProposalState,
    EnrollmentRecoveryProjection,
    ManifestRef,
    OwnerDomainId,
)

from eidolon_admin_server.app.control_plane.admission_intents import (
    SqliteAdmissionDecisionIntentStore,
)
from eidolon_admin_server.app.control_plane.contracts import (
    ControllerEnrollmentDecisionIntent,
)
from eidolon_admin_server.app.control_plane.errors import AuthorityFailure
from eidolon_admin_server.app.control_plane.hub_credentials import HubAdminCredentialIssuer
from eidolon_admin_server.app.control_plane.service import ControlPlaneService

NOW = datetime(2026, 8, 24, tzinfo=UTC)
DOMAIN = OwnerDomainId("owner-domain-a")
BUSINESS_OWNER = BusinessOwnerId("owner_account_a")
MANIFEST = ManifestRef(
    manifest_id="manifest-a", revision=3, digest="sha256:" + "a" * 64
)
ACTOR = ControllerActorRef(
    principal_id="ectrl-0123456789abcdef0123",
    owner_domain_id=DOMAIN,
    granted_scopes=("device.read", "device.claim.approve"),
    authentication_strength="software",
)


def _payload(*, request_id: str = "decision-1", decision: str = "approve"):
    return ControllerEnrollmentDecisionIntent(
        contract_version="1",
        request_id=request_id,
        actor=ACTOR,
        decision=DecideEnrollment(
            enrollment_id="enrollment-a",
            expected_proposal_revision=7,
            decision=decision,
            target_owner_domain_id=DOMAIN,
            target_business_owner_id=BUSINESS_OWNER,
            reviewed_manifest_ref=MANIFEST,
        ),
    )


def _proposal(state: EnrollmentProposalState, revision: int = 7):
    return EnrollmentProposal(
        enrollment_id="enrollment-a",
        proposal_revision=revision,
        state=state,
        device_instance_candidate_id="device-a",
        requested_owner_domain_id=DOMAIN,
        hardware_evidence_digest="sha256:" + "b" * 64,
        manifest_ref=MANIFEST,
        handoff_key_id="sha256:" + "c" * 64,
        created_at=NOW,
        expires_at=NOW + timedelta(hours=1),
    )


class Hub:
    def __init__(self, *, lose_first_reply: bool = False, unavailable: bool = False):
        self.result: DecideEnrollmentResult | None = None
        self.calls: list[dict] = []
        self.lose_first_reply = lose_first_reply
        self.unavailable = unavailable

    async def get_enrollment_recovery(self, *, enrollment_id, authorization):
        if self.unavailable:
            raise AuthorityFailure("hub", "unavailable", "Hub unavailable", 503)
        approved = self.result is not None
        return EnrollmentRecoveryProjection(
            proposal=_proposal(
                EnrollmentProposalState.APPROVED_AWAITING_HANDOFF
                if approved
                else EnrollmentProposalState.PENDING_REVIEW,
                revision=8 if approved else 7,
            ),
            approval_decision=(
                ApprovalDecision(
                    decision_id=self.result.decision_id,
                    enrollment_id=enrollment_id,
                    decision=self.result.decision,
                    actor=ACTOR,
                    target_owner_domain_id=DOMAIN,
                    target_business_owner_id=BUSINESS_OWNER,
                    reviewed_manifest_ref=MANIFEST,
                    expected_proposal_revision=7,
                    decided_at=NOW,
                )
                if approved
                else None
            ),
            grant_delivery=None,
            claim=None,
            source_revision=8 if approved else 7,
            observed_at=NOW,
        )

    async def decide_enrollment(self, **values):
        self.calls.append(values)
        if self.result is None:
            self.result = DecideEnrollmentResult(
                decision_id="decision-a",
                decision=values["command"].decision,
                decided_by=ACTOR,
                decided_at=NOW,
                proposal_revision=8,
            )
            if self.lose_first_reply:
                self.lose_first_reply = False
                raise AuthorityFailure("hub", "unavailable", "reply lost", 503)
        return self.result


def _service(hub, *, store=None):
    return ControlPlaneService(
        directory=object(), data=object(), workspace=object(), hub=hub,
        kernel=object(), memory=object(), admission_intents=store,
        hub_credentials=HubAdminCredentialIssuer(secret=b"x" * 32, ttl_seconds=60),
    )


@pytest.mark.asyncio
async def test_explicit_decision_persists_and_returns_recoverable_projection() -> None:
    hub = Hub()
    result = await _service(hub).decide_controller_enrollment(payload=_payload())
    assert result.checkpoint == "decision_committed"
    assert result.recovery.proposal.state == "approved_awaiting_handoff"
    assert result.recovery.approval_decision.actor == ACTOR
    assert hub.calls[0]["command"] == _payload().decision
    assert hub.calls[0]["command_id"] == result.command_id


@pytest.mark.asyncio
async def test_reply_loss_replays_same_command_instead_of_creating_authority() -> None:
    hub = Hub(lose_first_reply=True)
    service = _service(hub)
    with pytest.raises(AuthorityFailure):
        await service.decide_controller_enrollment(payload=_payload())
    result = await service.decide_controller_enrollment(payload=_payload())
    assert result.checkpoint == "decision_committed"
    assert len(hub.calls) == 2
    assert hub.calls[0]["command_id"] == hub.calls[1]["command_id"]


@pytest.mark.asyncio
async def test_restart_resumes_durable_intent_with_same_command(tmp_path) -> None:
    hub = Hub(lose_first_reply=True)
    path = tmp_path / "intents.sqlite3"
    first_store = SqliteAdmissionDecisionIntentStore(path)
    with pytest.raises(AuthorityFailure):
        await _service(hub, store=first_store).decide_controller_enrollment(payload=_payload())
    first_store.close()
    second_store = SqliteAdmissionDecisionIntentStore(path)
    result = await _service(hub, store=second_store).decide_controller_enrollment(payload=_payload())
    second_store.close()
    assert result.command_id == hub.calls[0]["command_id"] == hub.calls[1]["command_id"]


@pytest.mark.asyncio
async def test_request_id_with_different_payload_is_a_conflict() -> None:
    service = _service(Hub())
    await service.decide_controller_enrollment(payload=_payload())
    with pytest.raises(AuthorityFailure) as caught:
        await service.decide_controller_enrollment(payload=_payload(decision="reject"))
    assert caught.value.status_code == 409


@pytest.mark.asyncio
async def test_owner_mismatch_is_rejected_before_hub_decision() -> None:
    class OtherOwnerHub(Hub):
        async def get_enrollment_recovery(self, **kwargs):
            recovery = await super().get_enrollment_recovery(**kwargs)
            return recovery.model_copy(
                update={
                    "proposal": recovery.proposal.model_copy(
                        update={
                            "requested_owner_domain_id": OwnerDomainId(
                                "owner-domain-b"
                            )
                        }
                    )
                }
            )

    hub = OtherOwnerHub()
    with pytest.raises(AuthorityFailure) as caught:
        await _service(hub).decide_controller_enrollment(payload=_payload())
    assert caught.value.status_code == 409
    assert hub.calls == []


@pytest.mark.asyncio
async def test_grant_delivered_remains_visible_in_recovery() -> None:
    class GrantDeliveredHub(Hub):
        async def get_enrollment_recovery(self, **kwargs):
            recovery = await super().get_enrollment_recovery(**kwargs)
            if self.result is None:
                return recovery
            return recovery.model_copy(
                update={
                    "proposal": recovery.proposal.model_copy(
                        update={"state": EnrollmentProposalState.GRANT_DELIVERED}
                    )
                }
            )

    result = await _service(GrantDeliveredHub()).decide_controller_enrollment(
        payload=_payload()
    )
    assert result.recovery.proposal.state == EnrollmentProposalState.GRANT_DELIVERED


@pytest.mark.asyncio
async def test_hub_unavailable_leaves_recoverable_intent_recorded(tmp_path) -> None:
    store = SqliteAdmissionDecisionIntentStore(tmp_path / "intents.sqlite3")
    with pytest.raises(AuthorityFailure) as caught:
        await _service(Hub(unavailable=True), store=store).decide_controller_enrollment(
            payload=_payload()
        )
    assert caught.value.status_code == 503
    row = store._connection.execute(
        "SELECT checkpoint, result_json FROM admission_decision_intents_v1"
    ).fetchone()
    store.close()
    assert tuple(row) == ("intent_recorded", None)
