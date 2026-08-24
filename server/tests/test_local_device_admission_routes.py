"""The Local API surface a Controller uses to read and decide Admission.

These are producer tests for the boundary the phone actually calls. The phone
never reaches Hub's canonical ``/api/admission/v1`` itself: Hub is the Admission
Authority, and Local is the workflow surface that presents a short-lived,
exactly-scoped Admission ActorContext on the Controller's behalf. Anything that
would let one origin answer for the other — an alias path here, a page cursor
this surface silently drops — belongs in this file as a failing test.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from eidolon_sdk.device_foundation.v1 import (
    AuthorityEndpoint,
    ClaimPage,
    EnrollmentProposalPage,
    EnrollmentProposalState,
    EnrollmentRecoveryProjection,
    LogicalAuthority,
    ManifestRef,
    OwnerDomainDescriptor,
)

from eidolon_admin_server.bootstrap.config import BootstrapMode, BootstrapSettings
from eidolon_admin_server.bootstrap.control import BootstrapControlClient
from eidolon_admin_server.local_api.app import create_app
from eidolon_admin_server.local_api.config import (
    LocalApiSettings,
    VerifiedOwnerDomainOnboardingTarget,
)
from eidolon_admin_server.app.control_plane.contracts import (
    AdmissionDecisionWorkflowResult,
)

pytestmark = pytest.mark.asyncio

_AUTH_CHALLENGE = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFG"
_CONTROLLER_ID = "ectrl-0123456789abcdefabcd"
_OWNER_DOMAIN = "owner-b0a862b0aab941d64554"
_BUSINESS_OWNER = "owner_683f0000000000000000"
_ENROLLMENT = "enrollment_UD70A3oYjWOTNROfI9GBlzId"
_NOW = datetime(2026, 8, 25, tzinfo=UTC)
_MANIFEST = ManifestRef(
    manifest_id="manifest-a", revision=2, digest="sha256:" + "a" * 64
)


def _descriptor() -> OwnerDomainDescriptor:
    return OwnerDomainDescriptor(
        owner_domain_id=_OWNER_DOMAIN,
        owner_domain_generation=3,
        directory_revision=2,
        trust_root_refs=("sha256:" + "b" * 64,),
        endpoints=(
            AuthorityEndpoint(
                authority=LogicalAuthority.ADMISSION,
                logical_audience="eidolon-hub",
                uri="https://eidolon-hub.local:9443/api/admission/v1",
                transport_profile="https-json",
                priority=0,
            ),
        ),
        issued_at=_NOW,
        expires_at=datetime(2027, 8, 25, tzinfo=UTC),
        signing_key_id="sha256:" + "c" * 64,
        signature="s" * 86,
    )


def _projection(state: str = "pending_review") -> dict:
    return {
        "proposal": {
            "enrollment_id": _ENROLLMENT,
            "proposal_revision": 2,
            "state": EnrollmentProposalState(state),
            "device_instance_candidate_id": "device-a",
            "requested_owner_domain_id": _OWNER_DOMAIN,
            "hardware_evidence_digest": "sha256:" + "d" * 64,
            "manifest_ref": _MANIFEST.model_dump(mode="json"),
            "handoff_key_id": "sha256:" + "e" * 64,
            "created_at": _NOW.isoformat(),
            "expires_at": "2026-08-26T00:00:00Z",
        },
        "approval_decision": None,
        "grant_delivery": None,
        "claim": None,
        "source_revision": 2,
        "observed_at": _NOW.isoformat(),
    }


class _AdmissionPort:
    """Records what the Local surface asked the control plane for."""

    def __init__(self) -> None:
        self.enrollment_queries: list = []
        self.claim_queries: list = []
        self.recoveries: list = []
        self.decisions: list = []

    async def query_enrollments(self, *, payload) -> EnrollmentProposalPage:
        self.enrollment_queries.append(payload)
        return EnrollmentProposalPage.model_validate(
            {
                "owner_domain_id": _OWNER_DOMAIN,
                "items": [_projection()],
                "next_cursor": {
                    "owner_domain_id": _OWNER_DOMAIN,
                    "sort_key": _NOW.isoformat(),
                    "resource_id": _ENROLLMENT,
                },
                "observed_at": _NOW.isoformat(),
            }
        )

    async def recover_enrollment(self, *, payload) -> EnrollmentRecoveryProjection:
        self.recoveries.append(payload)
        return EnrollmentRecoveryProjection.model_validate(_projection())

    async def query_claims(self, *, payload) -> ClaimPage:
        self.claim_queries.append(payload)
        return ClaimPage.model_validate(
            {
                "owner_domain_id": _OWNER_DOMAIN,
                "items": [],
                "next_cursor": None,
                "observed_at": _NOW.isoformat(),
            }
        )

    async def decide(self, *, payload) -> AdmissionDecisionWorkflowResult:
        self.decisions.append(payload)
        actor = payload.actor.model_dump(mode="json")
        recovery = _projection("approved_awaiting_handoff")
        recovery["approval_decision"] = {
            "decision_id": "decision-a",
            "enrollment_id": _ENROLLMENT,
            "decision": "approve",
            "actor": actor,
            "target_owner_domain_id": _OWNER_DOMAIN,
            "target_business_owner_id": _BUSINESS_OWNER,
            "reviewed_manifest_ref": _MANIFEST.model_dump(mode="json"),
            "expected_proposal_revision": 2,
            "decided_at": _NOW.isoformat(),
        }
        return AdmissionDecisionWorkflowResult.model_validate(
            {
                "operation": "admin.admission-decision-intent",
                "request_id": payload.request_id,
                "intent_id": "admission-intent-" + "a" * 32,
                "command_id": "decide-enrollment-" + "b" * 32,
                "checkpoint": "decision_committed",
                "decision_result": {
                    "decision_id": "decision-a",
                    "decision": "approve",
                    "decided_by": actor,
                    "decided_at": _NOW.isoformat(),
                    "proposal_revision": 2,
                },
                "recovery": recovery,
            }
        )

    async def close(self) -> None:
        return None


class _UnusedPort:
    def __getattr__(self, name):
        raise AssertionError(f"unexpected client call: {name}")

    async def close(self) -> None:
        return None


def _settings(tmp_path: Path) -> LocalApiSettings:
    root = tmp_path / "owner-root.pem"
    signer = tmp_path / "authority-signer.pem"
    for path in (root, signer):
        path.write_text("-----BEGIN CERTIFICATE-----\nMA==\n", encoding="ascii")
    return LocalApiSettings(
        bootstrap=BootstrapSettings(
            mode=BootstrapMode.DEVELOPMENT,
            state_dir=tmp_path / "state",
            runtime_dir=tmp_path / "run",
            control_socket=tmp_path / "run/control.sock",
            ble_service_uuid="179e2e95-b1ee-5aa5-8dcf-7519b6c7ac52",
        ),
        device_onboarding_target=VerifiedOwnerDomainOnboardingTarget(
            owner_domain_id=_OWNER_DOMAIN,
            descriptor_uri="https://eidolon-hub.local:9443/.well-known/eidolon-owner-domain",
            descriptor=_descriptor(),
            owner_root_certificate_path=root,
            authority_signing_certificate_path=signer,
        ),
    )


def _app(tmp_path: Path, admission) -> object:
    unused = _UnusedPort()
    return create_app(
        _settings(tmp_path),
        workspace_client=unused,  # type: ignore[arg-type]
        runtime_client=unused,  # type: ignore[arg-type]
        devices_client=unused,  # type: ignore[arg-type]
        device_admission_client=admission,  # type: ignore[arg-type]
        host_services_client=unused,  # type: ignore[arg-type]
    )


async def _headers(client: httpx.AsyncClient) -> dict[str, str]:
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
    assert session.status_code == 200, session.text
    return {"Authorization": f"Bearer {session.json()['access_token']}"}


def _controller_principal(monkeypatch: pytest.MonkeyPatch) -> None:
    principal = {
        "contract_version": "1",
        "controller_id": _CONTROLLER_ID,
        "owner_id": _BUSINESS_OWNER,
        "reset_epoch": 0,
    }

    async def bootstrap_request(self, operation: str, **_parameters):
        if operation in {"controller.authenticate", "controller.validate"}:
            return principal
        raise AssertionError(f"unexpected bootstrap operation: {operation}")

    monkeypatch.setattr(BootstrapControlClient, "request", bootstrap_request)


async def test_local_is_the_only_admission_surface_a_controller_can_reach(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hub's canonical path is not answered here, under any spelling.

    This is the regression that stalled a real Add: the phone kept Hub's path
    and swapped only the origin, and a 404 from an origin that never owned that
    path is indistinguishable from a Host that is merely behind.
    """

    _controller_principal(monkeypatch)
    port = _AdmissionPort()
    transport = httpx.ASGITransport(app=_app(tmp_path, port))
    async with httpx.AsyncClient(
        transport=transport, base_url="https://local.test"
    ) as client:
        headers = await _headers(client)
        aliases = [
            await client.get("/api/admission/v1/enrollments", headers=headers),
            await client.post("/api/admission/v1/enrollments", headers=headers, json={}),
            await client.get(
                f"/api/admission/v1/enrollments/{_ENROLLMENT}", headers=headers
            ),
            await client.get("/api/admission/v1/claims", headers=headers),
        ]

    assert [response.status_code for response in aliases] == [404, 404, 404, 404]
    assert port.enrollment_queries == []
    assert port.recoveries == []


async def test_enrollment_page_and_cursor_reach_the_admission_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _controller_principal(monkeypatch)
    port = _AdmissionPort()
    transport = httpx.ASGITransport(app=_app(tmp_path, port))
    async with httpx.AsyncClient(
        transport=transport, base_url="https://local.test"
    ) as client:
        headers = await _headers(client)
        anonymous = await client.get("/api/local/v1/device-enrollments")
        first = await client.get(
            "/api/local/v1/device-enrollments",
            headers=headers,
            params={"states": ["pending_review", "grant_acknowledged"], "limit": 50},
        )
        cursor = first.json()["next_cursor"]
        second = await client.get(
            "/api/local/v1/device-enrollments",
            headers=headers,
            params={
                "limit": 50,
                "after_sort_key": cursor["sort_key"],
                "after_resource_id": cursor["resource_id"],
            },
        )
        default = await client.get(
            "/api/local/v1/device-enrollments", headers=headers
        )
        half = await client.get(
            "/api/local/v1/device-enrollments",
            headers=headers,
            params={"after_resource_id": _ENROLLMENT},
        )

    assert anonymous.status_code == 401
    assert first.status_code == 200
    assert second.status_code == 200
    assert default.status_code == 200
    # A cursor missing half of itself would silently re-read page one.
    assert half.status_code == 422

    asked = port.enrollment_queries
    assert len(asked) == 3
    assert [str(state) for state in asked[0].query.states] == [
        "pending_review",
        "grant_acknowledged",
    ]
    assert asked[0].query.cursor is None
    assert asked[1].query.cursor is not None
    assert asked[1].query.cursor.resource_id == _ENROLLMENT
    assert str(asked[1].query.cursor.owner_domain_id) == _OWNER_DOMAIN
    # The queue of undecided Proposals is the approver's view: Hub shows an
    # Enrollment nobody has decided only to a principal who could decide it.
    assert asked[0].actor.granted_scopes == ("device.read", "device.claim.approve")
    # The default page includes acknowledged Grants: a device that finished its
    # handoff while the phone was closed must still be findable by its setup.
    assert "grant_acknowledged" in {str(state) for state in asked[2].query.states}


async def test_one_enrollment_is_readable_without_deciding_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _controller_principal(monkeypatch)
    port = _AdmissionPort()
    transport = httpx.ASGITransport(app=_app(tmp_path, port))
    async with httpx.AsyncClient(
        transport=transport, base_url="https://local.test"
    ) as client:
        headers = await _headers(client)
        anonymous = await client.get(f"/api/local/v1/device-enrollments/{_ENROLLMENT}")
        read = await client.get(
            f"/api/local/v1/device-enrollments/{_ENROLLMENT}", headers=headers
        )

    assert anonymous.status_code == 401
    assert read.status_code == 200
    assert read.json()["proposal"]["enrollment_id"] == _ENROLLMENT
    assert len(port.recoveries) == 1
    assert port.recoveries[0].enrollment_id == _ENROLLMENT
    assert str(port.recoveries[0].owner_domain_id) == _OWNER_DOMAIN
    assert port.recoveries[0].actor.granted_scopes == (
        "device.read",
        "device.claim.approve",
    )
    assert port.decisions == []


async def test_a_decision_carries_the_owner_the_phone_showed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _controller_principal(monkeypatch)
    port = _AdmissionPort()
    transport = httpx.ASGITransport(app=_app(tmp_path, port))
    body = {
        "contract_version": "1",
        "request_id": "mobile-decision-setup-1",
        "expected_proposal_revision": 2,
        "decision": "approve",
        "reviewed_manifest_ref": _MANIFEST.model_dump(mode="json"),
        "expected_owner_domain_id": _OWNER_DOMAIN,
        "expected_business_owner_id": _BUSINESS_OWNER,
        "target_space_id": None,
        "initial_assignment_intent": {"companion_id": "companion-a"},
        "initial_capability_policy_refs": [],
    }
    async with httpx.AsyncClient(
        transport=transport, base_url="https://local.test"
    ) as client:
        headers = await _headers(client)
        approved = await client.put(
            f"/api/local/v1/device-enrollments/{_ENROLLMENT}/decision",
            headers=headers,
            json=body,
        )
        crossed = await client.put(
            f"/api/local/v1/device-enrollments/{_ENROLLMENT}/decision",
            headers=headers,
            json={**body, "expected_owner_domain_id": "owner-someone-else"},
        )

    assert approved.status_code == 200
    result = approved.json()
    assert result["checkpoint"] == "decision_committed"
    assert result["recovery"]["approval_decision"]["decision_id"] == "decision-a"
    assert crossed.status_code == 409

    assert len(port.decisions) == 1
    intent = port.decisions[0]
    assert intent.request_id == "mobile-decision-setup-1"
    assert "device.claim.approve" in intent.actor.granted_scopes
    assert str(intent.decision.target_business_owner_id) == _BUSINESS_OWNER
    assert intent.decision.initial_assignment_intent == {"companion_id": "companion-a"}


async def test_claim_page_cursor_stays_inside_this_owner_domain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _controller_principal(monkeypatch)
    port = _AdmissionPort()
    transport = httpx.ASGITransport(app=_app(tmp_path, port))
    async with httpx.AsyncClient(
        transport=transport, base_url="https://local.test"
    ) as client:
        headers = await _headers(client)
        listed = await client.get(
            "/api/local/v1/device-claims",
            headers=headers,
            params={
                "after_sort_key": _NOW.isoformat(),
                "after_resource_id": "device-a",
            },
        )

    assert listed.status_code == 200
    cursor = port.claim_queries[0].query.cursor
    assert cursor is not None
    assert str(cursor.owner_domain_id) == _OWNER_DOMAIN
