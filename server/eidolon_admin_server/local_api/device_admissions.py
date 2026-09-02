"""Controller-scoped facade over Admin's canonical Admission workflow."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.parse import quote

import httpx
from eidolon_sdk.device_foundation.v1 import (
    ActorRef,
    BusinessOwnerId,
    ClaimPage,
    ClaimQuery,
    ControllerActorRef,
    DecideEnrollment,
    DeviceRef,
    EnrollmentProposalPage,
    EnrollmentProposalQuery,
    EnrollmentRecoveryProjection,
    ManifestRef,
    OwnerAuthorizationContext,
    OwnerDomainDescriptor,
    OwnerDomainId,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..app.control_plane.contracts import (
    AdmissionDecisionWorkflowResult,
    CommissioningVoucherIssued,
    ControllerClaimQuery,
    ControllerCommissioningVoucherRequest,
    ControllerDeviceRemovalRequest,
    ControllerEnrollmentDecisionIntent,
    ControllerEnrollmentQuery,
    ControllerEnrollmentRecoveryQuery,
    DeviceRemovalResult,
    RemovalCondition,
)
from ..lifecycle_workflow.protocol import (
    LifecycleRemovalCall,
    LifecycleWorkflowReply,
    RemovalOwnerAuthorizationContext,
    read_frame,
    removal_intent_id,
    write_frame,
)
from .config import VerifiedOwnerDomainOnboardingTarget

_LOGGER = logging.getLogger(__name__)
_REFUSAL_REASONS = {
    "conflict": "主机不接受这台设备当前的状态。请刷新列表后重试。",
    "not_found": "主机上已经没有这台设备了。",
    "unauthorized": "主机不再授权这台手机管理设备。",
    "forbidden": "主机不再授权这台手机管理设备。",
    "invalid_request": "主机拒绝了这次请求的内容。",
    "unavailable": "主机的设备权威暂时不可用。",
    "configuration": "主机的设备权威尚未配置完成。",
    "upstream_failure": "主机的设备权威没有应答。",
    "contract_violation": "主机的设备权威返回了不符合契约的应答。",
}


class DeviceAdmissionError(RuntimeError):
    def __init__(
        self, message: str, *, status_code: int = 503, reason: str | None = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.reason = reason


def device_admission_detail(exc: DeviceAdmissionError) -> str | dict[str, str]:
    """The HTTP body for this refusal. Not a reason string — see below."""

    return {"reason": exc.reason} if exc.reason is not None else str(exc)


def device_admission_reason(exc: DeviceAdmissionError) -> str:
    """The one sentence a person is shown, always a string.

    Kept apart from the body because they are different facts with different
    types, and one function answering both made the type depend on whether a
    reason happened to be set. The refusal builder truncates what it is given,
    so a structured body reaching it raises TypeError and turns a refusal the
    caller could act on into a 500. Nothing set a reason on the removal path,
    so that stayed unreachable right up until this change set one.
    """

    return exc.reason if exc.reason is not None else str(exc)


#: Which refusal each Lifecycle Workflow problem is, in the vocabulary the
#: phone's reason table is keyed by.
#:
#: Removal answers over a Unix socket, not HTTP, so it never passed through
#: ``_refusal`` and never consulted ``_REFUSAL_REASONS``. Every removal refusal
#: reached the phone as English operator prose or as a kind guessed from a
#: status code. The workflow states its own closed vocabulary, so it is mapped
#: here once, and a test requires the mapping to cover that vocabulary exactly
#: — a new problem code cannot ship without a sentence for the person reading it.
_WORKFLOW_PROBLEM_KINDS = {
    "AUTHN_INVALID": "unauthorized",
    "AUTHZ_DENIED": "forbidden",
    "INVALID_REQUEST": "invalid_request",
    "WORKFLOW_UNAVAILABLE": "unavailable",
    "WORKFLOW_FAILURE": "upstream_failure",
}


def workflow_problem_reason(code: str) -> str | None:
    return _REFUSAL_REASONS.get(_WORKFLOW_PROBLEM_KINDS.get(code, ""))


def _refusal(response: httpx.Response, *, operation: str) -> DeviceAdmissionError:
    authority = kind = words = None
    try:
        document = response.json()
        detail = document.get("detail") if isinstance(document, dict) else None
        if isinstance(detail, dict):
            authority, kind, words = (
                detail.get("authority"),
                detail.get("kind"),
                detail.get("detail"),
            )
    except ValueError:
        pass
    if words:
        _LOGGER.warning(
            "Admin Device %s refused by %s authority (%s): %s",
            operation,
            authority or "an unnamed",
            kind or "unknown kind",
            words,
        )
    return DeviceAdmissionError(
        f"Admin Device {operation} did not complete",
        status_code=response.status_code
        if response.status_code in {401, 403, 404, 409, 422, 502, 503}
        else 503,
        reason=_REFUSAL_REASONS.get(kind or ""),
    )


class LocalDeviceOnboardingTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["local.device-onboarding-target"] = (
        "local.device-onboarding-target"
    )
    contract_version: Literal["1"] = "1"
    owner_domain_id: str = Field(min_length=1, max_length=128)
    owner_domain_descriptor: OwnerDomainDescriptor
    owner_root_certificate: str = Field(
        min_length=1, max_length=4096, pattern=r"^-----BEGIN CERTIFICATE-----"
    )
    authority_signing_certificate: str = Field(
        min_length=1, max_length=4096, pattern=r"^-----BEGIN CERTIFICATE-----"
    )

    @classmethod
    def from_verified(
        cls, target: VerifiedOwnerDomainOnboardingTarget
    ) -> LocalDeviceOnboardingTarget:
        return cls(
            owner_domain_id=target.owner_domain_id,
            owner_domain_descriptor=target.descriptor,
            owner_root_certificate=target.owner_root_certificate_path.read_text(
                encoding="ascii"
            ),
            authority_signing_certificate=target.authority_signing_certificate_path.read_text(
                encoding="ascii"
            ),
        )


class LocalEnrollmentDecisionRequest(BaseModel):
    """Explicit Owner action; Hub's Decision command remains the canonical DTO."""

    model_config = ConfigDict(extra="forbid")
    contract_version: Literal["1"]
    request_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    expected_proposal_revision: int = Field(ge=1)
    decision: Literal["approve", "reject"]
    reviewed_manifest_ref: ManifestRef
    #: The Owner the confirming screen said this device was joining. The session
    #: is what authorizes the Decision, but the phone must still say which Owner
    #: it showed: a Controller whose Host changed Owner underneath it would
    #: otherwise approve a device into an Owner the user never saw.
    expected_owner_domain_id: str = Field(min_length=1, max_length=128)
    expected_business_owner_id: str = Field(min_length=1, max_length=64)
    target_space_id: str | None = Field(default=None, min_length=3, max_length=128)
    initial_assignment_intent: dict[str, Any] | None = None
    initial_capability_policy_refs: tuple[str, ...] = ()

    def to_admin(
        self,
        *,
        enrollment_id: str,
        owner_domain_id: OwnerDomainId,
        business_owner_id: BusinessOwnerId,
        controller_id: str,
    ) -> ControllerEnrollmentDecisionIntent:
        expected = (self.expected_owner_domain_id, self.expected_business_owner_id)
        if expected != (str(owner_domain_id), str(business_owner_id)):
            raise DeviceAdmissionError(
                "Decision named an Owner this session does not hold",
                status_code=409,
                reason=_REFUSAL_REASONS["conflict"],
            )
        actor = admission_actor(
            controller_id=controller_id, owner_domain_id=owner_domain_id
        )
        return ControllerEnrollmentDecisionIntent(
            contract_version="1",
            request_id=self.request_id,
            actor=actor,
            decision=DecideEnrollment(
                enrollment_id=enrollment_id,
                expected_proposal_revision=self.expected_proposal_revision,
                decision=self.decision,
                target_owner_domain_id=owner_domain_id,
                target_business_owner_id=business_owner_id,
                target_space_id=self.target_space_id,
                reviewed_manifest_ref=self.reviewed_manifest_ref,
                initial_assignment_intent=self.initial_assignment_intent,
                initial_capability_policy_refs=self.initial_capability_policy_refs,
            ),
        )


def admission_actor(
    *, controller_id: str, owner_domain_id: OwnerDomainId
) -> ControllerActorRef:
    """This Controller's authority, which does not change with the operation.

    It used to: reads were sent as `device.read` alone and only the Decision
    carried `device.claim.approve`. That describes the request, not the
    principal — and it made the one screen this product needs impossible, because
    a Proposal nobody has decided yet is visible only to whoever could decide it.
    A Controller authenticated to this Owner's Host is that person; the Authority
    still authorizes every command on its own terms.
    """

    return ControllerActorRef(
        principal_id=controller_id,
        owner_domain_id=owner_domain_id,
        granted_scopes=("device.read", "device.claim.approve"),
        authentication_strength="software",
    )


def enrollment_query(
    *,
    controller_id: str,
    owner_domain_id: OwnerDomainId,
    business_owner_id: BusinessOwnerId,
    query: EnrollmentProposalQuery,
) -> ControllerEnrollmentQuery:
    return ControllerEnrollmentQuery(
        contract_version="1",
        actor=admission_actor(
            controller_id=controller_id, owner_domain_id=owner_domain_id
        ),
        business_owner_id=business_owner_id,
        query=query,
    )


def commissioning_voucher_request(
    *,
    controller_id: str,
    owner_domain_id: OwnerDomainId,
    business_owner_id: BusinessOwnerId,
    operational_spki_sha256: str,
) -> ControllerCommissioningVoucherRequest:
    return ControllerCommissioningVoucherRequest(
        contract_version="1",
        actor=admission_actor(
            controller_id=controller_id, owner_domain_id=owner_domain_id
        ),
        business_owner_id=business_owner_id,
        owner_domain_id=owner_domain_id,
        operational_spki_sha256=operational_spki_sha256,
    )


def enrollment_recovery_query(
    *,
    controller_id: str,
    owner_domain_id: OwnerDomainId,
    business_owner_id: BusinessOwnerId,
    enrollment_id: str,
) -> ControllerEnrollmentRecoveryQuery:
    return ControllerEnrollmentRecoveryQuery(
        contract_version="1",
        actor=admission_actor(
            controller_id=controller_id, owner_domain_id=owner_domain_id
        ),
        business_owner_id=business_owner_id,
        owner_domain_id=owner_domain_id,
        enrollment_id=enrollment_id,
    )


def claim_query(
    *,
    controller_id: str,
    owner_domain_id: OwnerDomainId,
    business_owner_id: BusinessOwnerId,
    query: ClaimQuery,
) -> ControllerClaimQuery:
    return ControllerClaimQuery(
        contract_version="1",
        actor=admission_actor(
            controller_id=controller_id, owner_domain_id=owner_domain_id
        ),
        business_owner_id=business_owner_id,
        query=query,
    )


class LocalDeviceRemovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contract_version: Literal["1"]
    request_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")

    def to_admin(
        self, *, device_id: str, owner_id: str, controller_id: str
    ) -> ControllerDeviceRemovalRequest:
        return ControllerDeviceRemovalRequest(
            contract_version="1",
            request_id=self.request_id,
            owner_id=owner_id,
            controller_id=controller_id,
            device_id=device_id,
            reason="owner-removed",
        )


class LocalDeviceRemovalProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["local.device-removal-progress"] = (
        "local.device-removal-progress"
    )
    contract_version: Literal["1"] = "1"
    request_id: str
    device_id: str
    owner_id: str
    intent_id: str
    outcome: Literal["done", "unfinished", "refused"]
    conditions: tuple[RemovalCondition, ...]


class AdminDeviceAdmissionPort(Protocol):
    async def query_enrollments(
        self, *, payload: ControllerEnrollmentQuery
    ) -> EnrollmentProposalPage: ...
    async def recover_enrollment(
        self, *, payload: ControllerEnrollmentRecoveryQuery
    ) -> EnrollmentRecoveryProjection: ...
    async def query_claims(self, *, payload: ControllerClaimQuery) -> ClaimPage: ...
    async def decide(
        self, *, payload: ControllerEnrollmentDecisionIntent
    ) -> AdmissionDecisionWorkflowResult: ...
    async def remove(
        self,
        *,
        payload: ControllerDeviceRemovalRequest,
        controller_reset_epoch: int,
        authorization_expires_at: datetime,
        target_device_ref: DeviceRef,
    ) -> DeviceRemovalResult: ...
    async def close(self) -> None: ...


class AdminDeviceAdmissionClient:
    def __init__(
        self,
        *,
        base_url: str,
        service_token: str,
        timeout_seconds: float,
        workflow_socket_path: Path = Path("/run/eidolon-lifecycle/workflow.sock"),
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = service_token.strip()
        self._timeout = timeout_seconds
        self._workflow_socket_path = workflow_socket_path
        self._client = client or httpx.AsyncClient(trust_env=False)
        self._owns_client = client is None

    def _headers(self) -> dict[str, str]:
        if not self._token:
            raise DeviceAdmissionError(
                "Local API Admin service credential is not configured"
            )
        return {"Authorization": f"Bearer {self._token}"}

    async def _post(self, path: str, payload: BaseModel, model):
        try:
            response = await self._client.post(
                f"{self._base_url}/api/control-plane/v1/{path}",
                headers=self._headers(),
                json=payload.model_dump(mode="json"),
                timeout=self._timeout,
            )
        except (
            httpx.TimeoutException,
            httpx.NetworkError,
            httpx.RemoteProtocolError,
        ) as exc:
            raise DeviceAdmissionError(
                "Admin Device admission control plane is unavailable"
            ) from exc
        if response.status_code != 200:
            raise _refusal(response, operation="admission query")
        try:
            return model.model_validate_json(response.content)
        except (ValueError, TypeError, ValidationError) as exc:
            raise DeviceAdmissionError(
                "Admin Device admission response violated its contract"
            ) from exc

    async def query_enrollments(
        self, *, payload: ControllerEnrollmentQuery
    ) -> EnrollmentProposalPage:
        page = await self._post(
            "admission/enrollment-queries", payload, EnrollmentProposalPage
        )
        if page.owner_domain_id != payload.query.owner_domain_id:
            raise DeviceAdmissionError(
                "Admin enrollment query returned another Owner Domain", status_code=502
            )
        return page

    async def issue_commissioning_voucher(
        self, *, payload: ControllerCommissioningVoucherRequest
    ) -> CommissioningVoucherIssued:
        return await self._post(
            "admission/commissioning-vouchers", payload, CommissioningVoucherIssued
        )

    async def recover_enrollment(
        self, *, payload: ControllerEnrollmentRecoveryQuery
    ) -> EnrollmentRecoveryProjection:
        projection = await self._post(
            "admission/enrollment-recoveries", payload, EnrollmentRecoveryProjection
        )
        if (
            projection.proposal.requested_owner_domain_id != payload.owner_domain_id
            or projection.proposal.enrollment_id != payload.enrollment_id
        ):
            raise DeviceAdmissionError(
                "Admin Enrollment recovery answered about another Enrollment",
                status_code=502,
            )
        return projection

    async def query_claims(self, *, payload: ControllerClaimQuery) -> ClaimPage:
        page = await self._post("admission/claim-queries", payload, ClaimPage)
        if page.owner_domain_id != payload.query.owner_domain_id:
            raise DeviceAdmissionError(
                "Admin Claim query returned another Owner Domain", status_code=502
            )
        return page

    async def decide(
        self, *, payload: ControllerEnrollmentDecisionIntent
    ) -> AdmissionDecisionWorkflowResult:
        try:
            response = await self._client.put(
                f"{self._base_url}/api/control-plane/v1/admission/decision-intents/{quote(payload.decision.enrollment_id, safe='')}",
                headers=self._headers(),
                json=payload.model_dump(mode="json"),
                timeout=self._timeout,
            )
        except (
            httpx.TimeoutException,
            httpx.NetworkError,
            httpx.RemoteProtocolError,
        ) as exc:
            raise DeviceAdmissionError(
                "Admin Device admission control plane is unavailable"
            ) from exc
        if response.status_code != 200:
            raise _refusal(response, operation="Decision")
        try:
            result = AdmissionDecisionWorkflowResult.model_validate_json(
                response.content
            )
        except (ValueError, TypeError, ValidationError) as exc:
            raise DeviceAdmissionError(
                "Admin Device Decision response violated its contract"
            ) from exc
        if result.request_id != payload.request_id:
            raise DeviceAdmissionError(
                "Admin Device Decision returned another request", status_code=409
            )
        return result

    async def remove(
        self,
        *,
        payload: ControllerDeviceRemovalRequest,
        controller_reset_epoch: int,
        authorization_expires_at: datetime,
        target_device_ref: DeviceRef,
    ) -> DeviceRemovalResult:
        owner_domain_id = target_device_ref.owner_domain_id
        intent_id = removal_intent_id(
            ingress_request_id=payload.request_id, owner_domain_id=str(owner_domain_id)
        )
        try:
            reply = await asyncio.wait_for(
                self._remove_over_workflow_socket(
                    LifecycleRemovalCall(
                        payload=payload,
                        authorization_context=RemovalOwnerAuthorizationContext(
                            controller_grant_generation=controller_reset_epoch,
                            reset_epoch=controller_reset_epoch,
                            owner_authorization_context=OwnerAuthorizationContext(
                                workload_principal_id="eidolon-lifecycle-workflow",
                                actor=ActorRef(
                                    principal_id=payload.controller_id,
                                    principal_type="controller",
                                    owner_domain_id=owner_domain_id,
                                    granted_scopes=(
                                        "device.read",
                                        "device.claim.revoke",
                                    ),
                                    authentication_strength="software",
                                ),
                                authorized_owner_domain_id=owner_domain_id,
                                scopes=("device.read", "device.claim.revoke"),
                                intent_id=intent_id,
                                target_device_ref=target_device_ref,
                                issued_at=datetime.now(UTC),
                                expires_at=authorization_expires_at,
                            ),
                        ),
                    )
                ),
                timeout=self._timeout,
            )
        except (TimeoutError, OSError, ValueError, asyncio.IncompleteReadError) as exc:
            raise DeviceAdmissionError("Lifecycle Workflow is unavailable") from exc
        if reply.problem is not None:
            raise DeviceAdmissionError(
                reply.problem.detail,
                status_code=reply.problem.status_code,
                reason=workflow_problem_reason(reply.problem.code),
            )
        if reply.result is None or reply.result.request_id != payload.request_id:
            raise DeviceAdmissionError(
                "Lifecycle Workflow returned another request", status_code=409
            )
        return reply.result

    async def _remove_over_workflow_socket(
        self, call: LifecycleRemovalCall
    ) -> LifecycleWorkflowReply:
        reader, writer = await asyncio.open_unix_connection(
            path=str(self._workflow_socket_path)
        )
        try:
            await write_frame(writer, call)
            return LifecycleWorkflowReply.model_validate(await read_frame(reader))
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (BrokenPipeError, ConnectionError, OSError):
                pass

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def device_removal_progress(
    *, owner_id: str, device_id: str, result: DeviceRemovalResult
) -> LocalDeviceRemovalProgress:
    hub = result.hub
    if hub is not None and (
        hub.device_ref.device_instance_id != device_id
        or hub.device_ref != result.device_ref
        or hub.lifecycle_state != "revoked"
    ):
        raise DeviceAdmissionError(
            "Admin Device removal did not confirm the requested device", status_code=502
        )
    required = {
        "platform_access_revoked",
        "mount_removed",
        "device_erase_acknowledged",
    }
    if {condition.name for condition in result.conditions} != required:
        raise DeviceAdmissionError(
            "Admin Device removal returned an incomplete condition projection",
            status_code=502,
        )
    return LocalDeviceRemovalProgress(
        request_id=result.request_id,
        device_id=device_id,
        owner_id=owner_id,
        intent_id=result.intent_id,
        outcome="done"
        if result.outcome == "completed"
        else "unfinished"
        if result.outcome == "accepted"
        else "refused",
        conditions=result.conditions,
    )
