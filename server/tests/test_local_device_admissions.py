from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
from eidolon_sdk.device_foundation.v1 import (
    BusinessOwnerId, ClaimPage, ClaimQuery, ClaimState, DeviceRef,
    EnrollmentProposalPage, ManifestRef, OwnerDomainId,
)

from eidolon_admin_server.app.control_plane.contracts import (
    AdmissionDecisionWorkflowResult, DeviceRemovalResult, RemovalCondition,
)
from eidolon_admin_server.local_api.device_admissions import (
    AdminDeviceAdmissionClient, DeviceAdmissionError,
    LocalEnrollmentDecisionRequest, claim_query, device_removal_progress,
)

NOW = datetime(2026, 8, 24, tzinfo=UTC)
DOMAIN = OwnerDomainId("owner-domain-a")
BUSINESS_OWNER = BusinessOwnerId("owner_account_a")
MANIFEST = ManifestRef(
    manifest_id="manifest-a", revision=1, digest="sha256:" + "a" * 64
)


def _local_request() -> LocalEnrollmentDecisionRequest:
    return LocalEnrollmentDecisionRequest(
        contract_version="1", request_id="local-decision-1",
        expected_proposal_revision=1, decision="approve",
        reviewed_manifest_ref=MANIFEST,
    )


def _admin_payload():
    return _local_request().to_admin(
        enrollment_id="enrollment-a", owner_domain_id=DOMAIN,
        business_owner_id=BUSINESS_OWNER,
        controller_id="ectrl-0123456789abcdef0123",
    )


def test_local_decision_derives_explicit_actor_and_owner_context() -> None:
    payload = _admin_payload()
    assert payload.actor.owner_domain_id == DOMAIN
    assert payload.actor.granted_scopes == ("device.read", "device.claim.approve")
    assert payload.decision.target_business_owner_id == BUSINESS_OWNER
    assert payload.decision.reviewed_manifest_ref == MANIFEST


@pytest.mark.asyncio
async def test_local_admin_adapter_uses_only_canonical_decision_route() -> None:
    payload = _admin_payload()
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request):
        seen.append(request)
        actor = payload.actor.model_dump(mode="json")
        proposal = {
            "enrollment_id": "enrollment-a", "proposal_revision": 2,
            "state": "approved_awaiting_handoff", "device_instance_candidate_id": "device-a",
            "requested_owner_domain_id": str(DOMAIN),
            "hardware_evidence_digest": "sha256:" + "b" * 64,
            "manifest_ref": MANIFEST.model_dump(mode="json"),
            "handoff_key_id": "sha256:" + "c" * 64,
            "created_at": NOW.isoformat(), "expires_at": "2026-08-25T00:00:00Z",
        }
        return httpx.Response(200, json={
            "operation": "admin.admission-decision-intent",
            "request_id": payload.request_id,
            "intent_id": "admission-intent-" + "a" * 32,
            "command_id": "decide-enrollment-" + "b" * 32,
            "checkpoint": "decision_committed",
            "decision_result": {
                "decision_id": "decision-a", "decision": "approve",
                "decided_by": actor, "decided_at": NOW.isoformat(),
                "proposal_revision": 2,
            },
            "recovery": {
                "proposal": proposal,
                "approval_decision": {
                    "decision_id": "decision-a", "enrollment_id": "enrollment-a",
                    "decision": "approve", "actor": actor,
                    "target_owner_domain_id": str(DOMAIN),
                    "target_business_owner_id": str(BUSINESS_OWNER),
                    "reviewed_manifest_ref": MANIFEST.model_dump(mode="json"),
                    "expected_proposal_revision": 1, "decided_at": NOW.isoformat(),
                },
                "grant_delivery": None, "claim": None,
                "source_revision": 2, "observed_at": NOW.isoformat(),
            },
        })

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = AdminDeviceAdmissionClient(
        base_url="http://127.0.0.1:9000", service_token="service-token",
        timeout_seconds=1, client=http,
    )
    result = await client.decide(payload=payload)
    await http.aclose()
    assert isinstance(result, AdmissionDecisionWorkflowResult)
    assert seen[0].url.path == "/api/control-plane/v1/admission/decision-intents/enrollment-a"
    assert seen[0].headers["authorization"] == "Bearer service-token"


@pytest.mark.asyncio
async def test_cross_owner_claim_page_is_rejected_not_filtered_to_empty() -> None:
    other = OwnerDomainId("owner-domain-b")
    page = ClaimPage(owner_domain_id=other, items=(), next_cursor=None, observed_at=NOW)

    async def handler(_request: httpx.Request):
        return httpx.Response(200, json=page.model_dump(mode="json"))

    payload = claim_query(
        controller_id="ectrl-0123456789abcdef0123", owner_domain_id=DOMAIN,
        business_owner_id=BUSINESS_OWNER,
        query=ClaimQuery(
            owner_domain_id=DOMAIN, states=(ClaimState.ACTIVE,), cursor=None, limit=20
        ),
    )
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = AdminDeviceAdmissionClient(
        base_url="http://127.0.0.1:9000", service_token="service-token",
        timeout_seconds=1, client=http,
    )
    with pytest.raises(DeviceAdmissionError) as caught:
        await client.query_claims(payload=payload)
    await http.aclose()
    assert caught.value.status_code == 502


def test_removal_requires_exact_four_condition_projection() -> None:
    ref = DeviceRef(
        device_instance_id="device-a", owner_domain_id=DOMAIN,
        owner_domain_generation=1, claim_generation=2, trust_epoch=3,
    )
    conditions = tuple(
        RemovalCondition(
            name=name,
            state="unknown" if name == "device_erase_acknowledged" else "true",
            authority=(
                "hub" if name == "platform_access_revoked"
                else "kernel" if name == "mount_removed" else "device-control"
            ),
            observed_at=NOW,
        )
        for name in (
            "platform_access_revoked", "mount_removed",
            "channel_access_revoked", "device_erase_acknowledged",
        )
    )
    result = DeviceRemovalResult(
        request_id="remove-1", intent_id="removal-intent-" + "a" * 32,
        device_ref=ref, outcome="completed", completed_stage="converged",
        steps=(), conditions=conditions,
    )
    assert device_removal_progress(
        owner_id=str(BUSINESS_OWNER), device_id="device-a", result=result
    ).outcome == "done"
    with pytest.raises(DeviceAdmissionError):
        device_removal_progress(
            owner_id=str(BUSINESS_OWNER), device_id="device-a",
            result=result.model_copy(update={"conditions": conditions[:-1]}),
        )


def test_empty_enrollment_page_is_only_an_observation_not_completion() -> None:
    page = EnrollmentProposalPage(
        owner_domain_id=DOMAIN, items=(), next_cursor=None, observed_at=NOW
    )
    assert page.items == ()
    assert not hasattr(page, "completed")
