from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import tempfile
from pathlib import Path
from typing import get_args

import httpx
import pytest
from eidolon_sdk.device_foundation.v1 import (
    BusinessOwnerId,
    ClaimPage,
    ClaimQuery,
    ClaimState,
    DeviceRef,
    EnrollmentProposalPage,
    ManifestRef,
    OwnerDomainId,
)

from eidolon_admin_server.app.control_plane.contracts import (
    AdmissionDecisionWorkflowResult,
    DeviceRemovalResult,
    RemovalCondition,
)
from eidolon_admin_server.app.control_plane.contracts import (
    ControllerDeviceRemovalRequest,
)
from eidolon_admin_server.lifecycle_workflow.protocol import (
    LifecycleWorkflowProblem,
    LifecycleWorkflowReply,
    read_frame,
    write_frame,
)
from eidolon_admin_server.local_api.management.router import refusal_for_status
from eidolon_admin_server.local_api.device_admissions import (
    _WORKFLOW_PROBLEM_KINDS,
    AdminDeviceAdmissionClient,
    DeviceAdmissionError,
    device_admission_detail,
    device_admission_reason,
    workflow_problem_reason,
    LocalEnrollmentDecisionRequest,
    claim_query,
    device_removal_progress,
)

from eidolon_sdk.device_foundation.v1.testing import named_device_instance_id

# Tests name the device they mean; the name becomes a real device
# instance id, which is a digest of a key and never a chosen string.
_DEVICE_A = named_device_instance_id("device-a")

NOW = datetime(2026, 8, 24, tzinfo=UTC)
DOMAIN = OwnerDomainId("owner-domain-a")
BUSINESS_OWNER = BusinessOwnerId("owner_account_a")
MANIFEST = ManifestRef(
    manifest_id="manifest-a", revision=1, digest="sha256:" + "a" * 64
)


def _local_request() -> LocalEnrollmentDecisionRequest:
    return LocalEnrollmentDecisionRequest(
        contract_version="1",
        request_id="local-decision-1",
        expected_proposal_revision=1,
        decision="approve",
        reviewed_manifest_ref=MANIFEST,
        expected_owner_domain_id=str(DOMAIN),
        expected_business_owner_id=str(BUSINESS_OWNER),
    )


def _admin_payload():
    return _local_request().to_admin(
        enrollment_id="enrollment-a",
        owner_domain_id=DOMAIN,
        business_owner_id=BUSINESS_OWNER,
        controller_id="ectrl-0123456789abcdef0123",
    )


def test_local_decision_refuses_an_owner_this_session_does_not_hold() -> None:
    """The phone showed an Owner; the session holds one. They must be the same.

    Approving into whatever Owner the session happens to carry would let a Host
    that changed Owner underneath an open confirmation screen collect a consent
    the user never gave.
    """

    request = LocalEnrollmentDecisionRequest(
        contract_version="1",
        request_id="local-decision-1",
        expected_proposal_revision=1,
        decision="approve",
        reviewed_manifest_ref=MANIFEST,
        expected_owner_domain_id="owner-domain-b",
        expected_business_owner_id=str(BUSINESS_OWNER),
    )
    with pytest.raises(DeviceAdmissionError) as refusal:
        request.to_admin(
            enrollment_id="enrollment-a",
            owner_domain_id=DOMAIN,
            business_owner_id=BUSINESS_OWNER,
            controller_id="ectrl-0123456789abcdef0123",
        )
    assert refusal.value.status_code == 409


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
            "enrollment_id": "enrollment-a",
            "proposal_revision": 2,
            "state": "approved_awaiting_handoff",
            "device_instance_candidate_id": _DEVICE_A,
            "requested_owner_domain_id": str(DOMAIN),
            "hardware_evidence_digest": "sha256:" + "b" * 64,
            "manifest_ref": MANIFEST.model_dump(mode="json"),
            "handoff_key_id": "sha256:" + "c" * 64,
            "created_at": NOW.isoformat(),
            "expires_at": "2026-08-25T00:00:00Z",
        }
        return httpx.Response(
            200,
            json={
                "operation": "admin.admission-decision-intent",
                "request_id": payload.request_id,
                "intent_id": "admission-intent-" + "a" * 32,
                "command_id": "decide-enrollment-" + "b" * 32,
                "checkpoint": "decision_committed",
                "decision_result": {
                    "decision_id": "decision-a",
                    "decision": "approve",
                    "decided_by": actor,
                    "decided_at": NOW.isoformat(),
                    "proposal_revision": 2,
                },
                "recovery": {
                    "proposal": proposal,
                    "approval_decision": {
                        "decision_id": "decision-a",
                        "enrollment_id": "enrollment-a",
                        "decision": "approve",
                        "actor": actor,
                        "target_owner_domain_id": str(DOMAIN),
                        "target_business_owner_id": str(BUSINESS_OWNER),
                        "reviewed_manifest_ref": MANIFEST.model_dump(mode="json"),
                        "expected_proposal_revision": 1,
                        "decided_at": NOW.isoformat(),
                    },
                    "grant_delivery": None,
                    "claim": None,
                    "source_revision": 2,
                    "observed_at": NOW.isoformat(),
                },
            },
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = AdminDeviceAdmissionClient(
        base_url="http://127.0.0.1:9000",
        service_token="service-token",
        timeout_seconds=1,
        client=http,
    )
    result = await client.decide(payload=payload)
    await http.aclose()
    assert isinstance(result, AdmissionDecisionWorkflowResult)
    assert (
        seen[0].url.path
        == "/api/control-plane/v1/admission/decision-intents/enrollment-a"
    )
    assert seen[0].headers["authorization"] == "Bearer service-token"


@pytest.mark.asyncio
async def test_cross_owner_claim_page_is_rejected_not_filtered_to_empty() -> None:
    other = OwnerDomainId("owner-domain-b")
    page = ClaimPage(owner_domain_id=other, items=(), next_cursor=None, observed_at=NOW)

    async def handler(_request: httpx.Request):
        return httpx.Response(200, json=page.model_dump(mode="json"))

    payload = claim_query(
        controller_id="ectrl-0123456789abcdef0123",
        owner_domain_id=DOMAIN,
        business_owner_id=BUSINESS_OWNER,
        query=ClaimQuery(
            owner_domain_id=DOMAIN, states=(ClaimState.ACTIVE,), cursor=None, limit=20
        ),
    )
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = AdminDeviceAdmissionClient(
        base_url="http://127.0.0.1:9000",
        service_token="service-token",
        timeout_seconds=1,
        client=http,
    )
    with pytest.raises(DeviceAdmissionError) as caught:
        await client.query_claims(payload=payload)
    await http.aclose()
    assert caught.value.status_code == 502


def test_removal_requires_exact_authority_condition_projection() -> None:
    ref = DeviceRef(
        device_instance_id=_DEVICE_A,
        owner_domain_id=DOMAIN,
        owner_domain_generation=1,
        claim_generation=2,
        trust_epoch=3,
    )
    conditions = tuple(
        RemovalCondition(
            name=name,
            state="unknown" if name == "device_erase_acknowledged" else "true",
            authority=(
                "hub"
                if name == "platform_access_revoked"
                else "kernel"
                if name == "mount_removed"
                else "device-control"
            ),
            observed_at=NOW,
        )
        for name in (
            "platform_access_revoked",
            "mount_removed",
            "device_erase_acknowledged",
        )
    )
    result = DeviceRemovalResult(
        request_id="remove-1",
        intent_id="removal-intent-" + "a" * 32,
        device_ref=ref,
        outcome="completed",
        completed_stage="converged",
        steps=(),
        conditions=conditions,
    )
    assert (
        device_removal_progress(
            owner_id=str(BUSINESS_OWNER), device_id=_DEVICE_A, result=result
        ).outcome
        == "done"
    )
    with pytest.raises(DeviceAdmissionError):
        device_removal_progress(
            owner_id=str(BUSINESS_OWNER),
            device_id=_DEVICE_A,
            result=result.model_copy(update={"conditions": conditions[:-1]}),
        )


def test_empty_enrollment_page_is_only_an_observation_not_completion() -> None:
    page = EnrollmentProposalPage(
        owner_domain_id=DOMAIN, items=(), next_cursor=None, observed_at=NOW
    )
    assert page.items == ()
    assert not hasattr(page, "completed")


def test_a_reason_is_a_sentence_and_a_body_is_a_body() -> None:
    """One function answering both made the type depend on the data.

    ``refusal_for_status`` truncates the reason it is given, so a structured
    body arriving there raises TypeError and turns a refusal the caller could
    act on into a 500. It stayed unreachable only because nothing on the
    removal path set a reason — which was itself the other half of the defect.
    """

    structured = DeviceAdmissionError("operator prose", status_code=409, reason="给人看的一句话")
    plain = DeviceAdmissionError("operator prose", status_code=503)

    assert device_admission_detail(structured) == {"reason": "给人看的一句话"}
    assert device_admission_reason(structured) == "给人看的一句话"
    assert device_admission_detail(plain) == "operator prose"
    assert device_admission_reason(plain) == "operator prose"

    # The refusal builder must accept what the reason function returns, for
    # both shapes. This is the call that produced the 500.
    for error in (structured, plain):
        refusal = refusal_for_status(error.status_code, device_admission_reason(error))
        assert isinstance(refusal.reason, str) and refusal.reason


def test_every_workflow_problem_has_a_sentence_for_the_person_reading_it() -> None:
    """Removal answers over a socket, so it never passed through ``_refusal``.

    The planned Chinese reason table was therefore never consulted on the one
    path an Owner uses to remove a device: refusals arrived as English operator
    prose. The mapping has to cover the workflow's own closed vocabulary, so a
    new problem code cannot ship without a sentence.
    """

    declared = set(get_args(LifecycleWorkflowProblem.model_fields["code"].annotation))

    assert set(_WORKFLOW_PROBLEM_KINDS) == declared
    for code in declared:
        reason = workflow_problem_reason(code)
        assert reason, code
        # A sentence for a person, not a kind name leaking through.
        assert reason not in _WORKFLOW_PROBLEM_KINDS.values()


@pytest.mark.asyncio
async def test_a_refused_removal_reaches_the_phone_as_a_sentence() -> None:
    """End to end over the real socket frame: refusal in, sentence out."""

    # A short path: AF_UNIX names are capped well below a pytest tmp_path.
    with tempfile.TemporaryDirectory(dir="/tmp") as directory:
        socket_path = Path(directory) / "w.sock"
        await _refused_removal_reaches_the_phone(socket_path)


async def _refused_removal_reaches_the_phone(socket_path: Path) -> None:

    async def _serve(reader, writer):
        await read_frame(reader)
        await write_frame(
            writer,
            LifecycleWorkflowReply(
                problem=LifecycleWorkflowProblem(
                    code="AUTHZ_DENIED",
                    detail="owner authorization was refused by the Hub",
                    status_code=403,
                )
            ),
        )
        await writer.drain()
        writer.close()

    server = await asyncio.start_unix_server(_serve, path=str(socket_path))
    try:
        client = AdminDeviceAdmissionClient(
            base_url="http://127.0.0.1:1",
            service_token="t" * 40,
            timeout_seconds=5.0,
            workflow_socket_path=socket_path,
        )
        with pytest.raises(DeviceAdmissionError) as failure:
            await client.remove(
                payload=ControllerDeviceRemovalRequest(
                    contract_version="1",
                    request_id="req-" + "a" * 20,
                    owner_id="owner-business-1",
                    controller_id="ectrl-" + "b" * 20,
                    device_id=named_device_instance_id("device-c"),
                    reason="owner-removed",
                ),
                controller_reset_epoch=1,
                # Relative, not a date. A fixed instant makes this pass or fail
                # by the wall clock: written before midnight UTC it was in the
                # future, and hours later the same code refused because the
                # authorization had "expired" — a green test that rots on a
                # timer says nothing about the code.
                authorization_expires_at=datetime.now(UTC) + timedelta(minutes=5),
                target_device_ref=DeviceRef(
                    device_instance_id=named_device_instance_id("device-c"),
                    owner_domain_id=DOMAIN,
                    owner_domain_generation=1,
                    claim_generation=1,
                    trust_epoch=1,
                ),
            )
    finally:
        server.close()
        await server.wait_closed()

    assert failure.value.status_code == 403
    assert failure.value.reason == "主机不再授权这台手机管理设备。"
    refusal = refusal_for_status(
        failure.value.status_code, device_admission_reason(failure.value)
    )
    assert refusal.reason == "主机不再授权这台手机管理设备。"
    assert refusal.kind == "denied"
