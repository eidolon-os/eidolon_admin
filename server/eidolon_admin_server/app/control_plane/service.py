"""Admin application orchestration across independent control authorities."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from datetime import UTC, datetime

import httpx
from eidolon_sdk.biz.system_data import CompanionRuntimeSnapshot
from eidolon_sdk.device_foundation.v1 import (
    ClaimPage,
    DecideEnrollment,
    EnrollmentProposalPage,
    EnrollmentProposalState,
    EnrollmentRecoveryProjection,
)

from ..settings import Settings
from .clients import (
    DataAuthorityClient,
    DataWorkspaceAuthorityClient,
    HubManagementClient,
    KernelMountClient,
    AgentActivityClient,
    MemoryRecollectionsClient,
    MemorySupervisorClient,
)
from .contracts import (
    AdmissionDecisionWorkflowResult,
    ControllerClaimQuery,
    ControllerEnrollmentDecisionIntent,
    ControllerEnrollmentQuery,
    ControllerCompanionAttachment,
    ControllerEnrollmentRecoveryQuery,
    BoundaryCapabilities,
    ControllerDeviceRemovalRequest,
    DeviceRemovalResult,
    KernelMount,
    KernelMountPage,
    OperatorDeviceAdmissionRequest,
    OperatorDeviceAdmissionResult,
    OperatorOwnerDeviceInventory,
    RemovalCondition,
    SourceStatus,
    WorkspaceInitializeRequest,
    WorkspaceOperation,
    WorkflowStep,
)
from .admission_intents import (
    InMemoryAdmissionDecisionIntentStore,
    SqliteAdmissionDecisionIntentStore,
)
from .directory import SystemDirectoryClient
from .errors import AuthorityFailure
from .hub_credentials import HubAdminCredentialIssuer
from ...lifecycle_workflow.protocol import RemovalOwnerAuthorizationContext


_OPERATOR_ENROLLMENT_STATES = (
    "pending_review",
    "approved_awaiting_handoff",
    "grant_delivered",
    "grant_acknowledged",
    "rejected",
    "expired",
    "canceled",
    "claim_revoked",
)


def _operator_child_request_id(request_id: str, step: str) -> str:
    return f"admin:{request_id}:{step}"


class ControlPlaneService:
    def __init__(
        self,
        *,
        directory: SystemDirectoryClient,
        data: DataAuthorityClient,
        workspace: DataWorkspaceAuthorityClient,
        hub: HubManagementClient,
        kernel: KernelMountClient,
        memory: MemoryRecollectionsClient,
        activity: AgentActivityClient,
        memory_supervisor: MemorySupervisorClient | None = None,
        hub_credentials: HubAdminCredentialIssuer | None = None,
        admission_intents=None,
        removal_intents=None,
        removal_observation_timeout_seconds: float = 0.0,
    ) -> None:
        self.directory = directory
        self.data = data
        self.workspace = workspace
        self.hub = hub
        self.kernel = kernel
        self.memory = memory
        self.activity = activity
        self.memory_supervisor = memory_supervisor
        self.hub_credentials = hub_credentials
        self.admission_intents = (
            admission_intents
            if admission_intents is not None
            else InMemoryAdmissionDecisionIntentStore()
        )
        if removal_intents is None:
            from .removal_intents import InMemoryRemovalIntentStore

            removal_intents = InMemoryRemovalIntentStore()
        self.removal_intents = removal_intents
        self.removal_observation_timeout_seconds = removal_observation_timeout_seconds

    def configured_authorities(self) -> frozenset[str]:
        """Which Owner-facing authorities this Host was actually given a key to.

        Answered here because this object is the only one that holds all the
        clients, and answered at all because ``/context`` promises a capability
        means "the code exists and its authority answers" — and for two years it
        only ever checked the first half. A Host installed before the memory and
        Agent credentials existed advertised every memory and conversation
        feature and failed all of them on contact.

        Deliberately a question about configuration and not about liveness. A
        credential either was or was not written into this Host; a service is up
        or down by the second, and a capability map that tracked the second one
        would make controls appear and vanish while someone was looking at them.
        Whether the authority is answering *now* is what a refusal is for, and
        refusals now say which kind they are.
        """

        return frozenset(
            name
            for name, client in (("memory", self.memory), ("agent", self.activity))
            if getattr(client, "has_credential", True)
        )

    @classmethod
    def build(
        cls,
        *,
        settings: Settings,
        http_client: httpx.AsyncClient,
    ) -> ControlPlaneService:
        directory = SystemDirectoryClient(
            base_url=settings.system_directory_url,
            uds_path=settings.system_directory_uds,
            timeout_seconds=settings.directory_timeout_seconds,
            client=None if settings.system_directory_uds else http_client,
        )
        return cls(
            directory=directory,
            data=DataAuthorityClient(
                directory=directory,
                client=http_client,
                service_token=settings.data_authority_token,
                timeout_seconds=settings.authority_timeout_seconds,
            ),
            workspace=DataWorkspaceAuthorityClient(
                directory=directory,
                client=http_client,
                service_token=settings.data_workspace_authority_token,
                timeout_seconds=settings.authority_timeout_seconds,
            ),
            hub=HubManagementClient(
                directory=directory,
                client=http_client,
                timeout_seconds=settings.authority_timeout_seconds,
            ),
            kernel=KernelMountClient(
                directory=directory,
                client=http_client,
                timeout_seconds=settings.authority_timeout_seconds,
            ),
            memory=MemoryRecollectionsClient(
                discovery_url=settings.memory_discovery_url,
                client=http_client,
                timeout_seconds=settings.authority_timeout_seconds,
                service_token=settings.memory_api_service_token,
            ),
            activity=AgentActivityClient(
                base_url=settings.agent_admin_url,
                client=http_client,
                timeout_seconds=settings.authority_timeout_seconds,
                service_token=settings.agent_admin_api_token,
            ),
            memory_supervisor=MemorySupervisorClient(
                base_url=settings.memory_supervisor_url,
                client=http_client,
                timeout_seconds=settings.authority_timeout_seconds,
            ),
            hub_credentials=HubAdminCredentialIssuer(
                secret=settings.hub_management_jwt_secret.get_secret_value().encode(),
                ttl_seconds=settings.hub_management_jwt_ttl_seconds,
            ),
            admission_intents=SqliteAdmissionDecisionIntentStore(
                settings.state_dir / "admission-decision-intents.sqlite3"
            ),
        )

    async def initialize_workspace(
        self,
        *,
        operation_id: str,
        payload: WorkspaceInitializeRequest,
    ) -> WorkspaceOperation:
        return await self.workspace.initialize(
            operation_id=operation_id,
            payload=payload,
        )

    async def get_workspace_operation(self, operation_id: str) -> WorkspaceOperation:
        return await self.workspace.get(operation_id)

    async def get_owner_default_runtime(
        self,
        owner_id: str,
    ) -> CompanionRuntimeSnapshot:
        return await self.data.get_owner_default_runtime(owner_id)

    async def close(self) -> None:
        await self.directory.close()
        close = getattr(self.admission_intents, "close", None)
        if close is not None:
            close()
        close = getattr(self.removal_intents, "close", None)
        if close is not None:
            close()

    async def operator_owner_inventory(
        self, *, owner_id: str, hub_authorization: str
    ) -> OperatorOwnerDeviceInventory:
        """Compose current Hub Claims and Kernel mounts for the Web page."""

        async def measured(call):
            started = time.perf_counter()
            try:
                value = await call
                return value, SourceStatus(
                    state="ok", latency_ms=(time.perf_counter() - started) * 1000
                )
            except AuthorityFailure as exc:
                return None, SourceStatus(
                    state="error",
                    latency_ms=(time.perf_counter() - started) * 1000,
                    failure=exc.to_wire(),
                )

        (claim_page, hub_status), (mount_page, kernel_status) = await asyncio.gather(
            measured(
                self.hub.list_authorized_claims(
                    authorization=hub_authorization,
                )
            ),
            measured(self.kernel.list_mounts(owner_id=owner_id)),
        )
        if hub_status.failure and hub_status.failure.kind in {
            "unauthorized",
            "forbidden",
        }:
            failure = hub_status.failure
            raise AuthorityFailure(
                "hub",
                failure.kind,
                failure.detail,
                401 if failure.kind == "unauthorized" else 403,
                failure.upstream_status,
                code=failure.code,
            )
        claims = tuple(
            claim
            for claim in (claim_page.items if claim_page is not None else ())
            if str(claim.business_owner_id) == owner_id
        )
        return OperatorOwnerDeviceInventory(
            owner_id=owner_id,
            degraded=claim_page is None or mount_page is None,
            hub=hub_status,
            kernel=kernel_status,
            claims=claims,
            mounts=mount_page.mounts if mount_page is not None else (),
        )

    async def admit_operator_device(
        self,
        payload: OperatorDeviceAdmissionRequest,
        *,
        hub_authorization: str,
    ) -> OperatorDeviceAdmissionResult:
        """Approve through current Hub Admission, then converge Kernel state."""

        page = await self.hub.list_authorized_enrollments(
            authorization=hub_authorization,
            states=_OPERATOR_ENROLLMENT_STATES,
        )
        matches = tuple(
            item
            for item in page.items
            if item.proposal.device_instance_candidate_id == payload.device_id
        )
        if not matches:
            raise AuthorityFailure(
                "hub", "not_found", "Hub has no Enrollment for this device", 404
            )
        active = tuple(
            item
            for item in matches
            if item.proposal.state
            in {
                EnrollmentProposalState.PENDING_REVIEW,
                EnrollmentProposalState.APPROVED_AWAITING_HANDOFF,
                EnrollmentProposalState.GRANT_DELIVERED,
                EnrollmentProposalState.GRANT_ACKNOWLEDGED,
            }
        )
        if len(active) != 1:
            raise AuthorityFailure(
                "hub",
                "conflict",
                "Hub Enrollment is terminal or ambiguous for this device",
                409,
            )
        recovery = active[0]
        proposal = recovery.proposal
        hub_request_id = _operator_child_request_id(payload.request_id, "hub-approve")
        hub_step_state = "replayed"
        if proposal.state == EnrollmentProposalState.PENDING_REVIEW:
            await self.hub.decide_enrollment(
                command=DecideEnrollment(
                    enrollment_id=proposal.enrollment_id,
                    expected_proposal_revision=proposal.proposal_revision,
                    decision="approve",
                    target_owner_domain_id=proposal.requested_owner_domain_id,
                    target_business_owner_id=payload.owner_id,
                    reviewed_manifest_ref=proposal.manifest_ref,
                ),
                command_id=hub_request_id,
                correlation_id=payload.request_id,
                authorization=hub_authorization,
            )
            recovery = await self.hub.get_enrollment_recovery(
                enrollment_id=proposal.enrollment_id,
                authorization=hub_authorization,
            )
            hub_step_state = "committed"
        decision = recovery.approval_decision
        if (
            decision is None
            or decision.decision != "approve"
            or str(decision.target_business_owner_id) != str(payload.owner_id)
        ):
            raise AuthorityFailure(
                "hub",
                "conflict",
                "Hub Enrollment was not approved for this Owner",
                409,
            )

        mount_request_id = _operator_child_request_id(payload.request_id, "kernel-mount")
        attach_request_id = _operator_child_request_id(payload.request_id, "kernel-attach")
        steps = [
            WorkflowStep(
                name="hub_approval",
                state=hub_step_state,
                request_id=hub_request_id,
            )
        ]
        mounted = await self._owner_mount(
            owner_id=str(payload.owner_id),
            device_id=payload.device_id,
            active_only=False,
        )
        if mounted is not None and mounted.active:
            steps.append(
                WorkflowStep(
                    name="kernel_mount", state="replayed", revision=mounted.revision
                )
            )
        else:
            try:
                result = await self.kernel.mount(
                    owner_id=str(payload.owner_id),
                    device_id=payload.device_id,
                    request_id=mount_request_id,
                    expected_revision=payload.expected_mount_revision,
                    replace_existing=payload.replace_existing_mount,
                )
                mounted = result.mount
                steps.append(
                    WorkflowStep(
                        name="kernel_mount",
                        state="replayed" if result.replayed else "committed",
                        request_id=mount_request_id,
                        revision=mounted.revision,
                    )
                )
            except AuthorityFailure as exc:
                waiting = exc
                retryable = exc.retryable or exc.kind in {
                    "not_found",
                    "unavailable",
                    "upstream_failure",
                }
                if exc.kind == "not_found":
                    waiting = AuthorityFailure(
                        "kernel",
                        "unavailable",
                        "Hub has approved the Enrollment; waiting for the device to acknowledge its Claim",
                        503,
                        exc.upstream_status,
                        True,
                    )
                steps.extend(
                    (
                        WorkflowStep(
                            name="kernel_mount",
                            state="failed",
                            request_id=mount_request_id,
                            failure=waiting.to_wire(),
                        ),
                        WorkflowStep(
                            name="companion_attachment",
                            state=(
                                "not_attempted"
                                if payload.companion_id is not None
                                else "not_requested"
                            ),
                            request_id=(
                                attach_request_id
                                if payload.companion_id is not None
                                else None
                            ),
                        ),
                    )
                )
                return OperatorDeviceAdmissionResult(
                    request_id=payload.request_id,
                    outcome="retry_required" if retryable else "blocked",
                    completed_stage="hub_approved",
                    recovery=(
                        "retry-forward-same-request-id"
                        if retryable
                        else "operator-action-required"
                    ),
                    steps=tuple(steps),
                )

        if payload.companion_id is None:
            steps.append(
                WorkflowStep(name="companion_attachment", state="not_requested")
            )
            return OperatorDeviceAdmissionResult(
                request_id=payload.request_id,
                outcome="completed",
                completed_stage="kernel_mounted",
                steps=tuple(steps),
                mount=mounted,
            )
        if mounted.attached_companion_id == payload.companion_id:
            steps.append(
                WorkflowStep(
                    name="companion_attachment",
                    state="replayed",
                    revision=mounted.revision,
                )
            )
            return OperatorDeviceAdmissionResult(
                request_id=payload.request_id,
                outcome="completed",
                completed_stage="companion_attached",
                steps=tuple(steps),
                mount=mounted,
            )
        try:
            attached = await self.kernel.attach(
                owner_id=str(payload.owner_id),
                device_id=payload.device_id,
                companion_id=payload.companion_id,
                request_id=attach_request_id,
                expected_revision=mounted.revision,
            )
        except AuthorityFailure as exc:
            steps.append(
                WorkflowStep(
                    name="companion_attachment",
                    state="failed",
                    request_id=attach_request_id,
                    revision=mounted.revision,
                    failure=exc.to_wire(),
                )
            )
            return OperatorDeviceAdmissionResult(
                request_id=payload.request_id,
                outcome="retry_required" if exc.retryable else "blocked",
                completed_stage="kernel_mounted",
                recovery=(
                    "retry-forward-same-request-id"
                    if exc.retryable
                    else "operator-action-required"
                ),
                steps=tuple(steps),
                mount=mounted,
            )
        steps.append(
            WorkflowStep(
                name="companion_attachment",
                state="replayed" if attached.replayed else "committed",
                request_id=attach_request_id,
                revision=attached.mount.revision,
            )
        )
        return OperatorDeviceAdmissionResult(
            request_id=payload.request_id,
            outcome="completed",
            completed_stage="companion_attached",
            steps=tuple(steps),
            mount=attached.mount,
        )

    async def list_enrollment_recovery(
        self, *, payload: ControllerEnrollmentQuery
    ) -> EnrollmentProposalPage:
        issuer = self._admission_issuer()
        return await self.hub.list_enrollment_recovery(
            query=payload.query,
            authorization=issuer.issue_admission_context(
                actor=payload.actor,
                business_owner_id=payload.business_owner_id,
            ),
        )

    async def get_enrollment_recovery(
        self, *, payload: ControllerEnrollmentRecoveryQuery
    ) -> EnrollmentRecoveryProjection:
        """Read one Enrollment's projection without deciding anything.

        The Decision workflow reads recovery too, but a Controller watching a
        device it just commissioned must be able to observe Hub's own
        progression — PendingReview, then Grant delivery, then Claim — without
        submitting an intent to learn it.
        """

        issuer = self._admission_issuer()
        projection = await self.hub.get_enrollment_recovery(
            enrollment_id=payload.enrollment_id,
            authorization=issuer.issue_admission_context(
                actor=payload.actor,
                business_owner_id=payload.business_owner_id,
            ),
        )
        if projection.proposal.requested_owner_domain_id != payload.owner_domain_id:
            raise AuthorityFailure(
                "hub",
                "contract_violation",
                "Hub Enrollment recovery crossed its requested Owner Domain",
                502,
            )
        return projection

    async def list_claims(self, *, payload: ControllerClaimQuery) -> ClaimPage:
        issuer = self._admission_issuer()
        return await self.hub.list_claims(
            query=payload.query,
            authorization=issuer.issue_admission_context(
                actor=payload.actor,
                business_owner_id=payload.business_owner_id,
            ),
        )

    async def decide_controller_enrollment(
        self, *, payload: ControllerEnrollmentDecisionIntent
    ) -> AdmissionDecisionWorkflowResult:
        """Persist one explicit Decision intent and resume it forward only."""

        issuer = self._admission_issuer()
        now = datetime.now(UTC)
        try:
            intent = self.admission_intents.get_or_create(
                ingress_request_id=payload.request_id,
                owner_domain_id=str(payload.decision.target_owner_domain_id),
                business_owner_id=str(payload.decision.target_business_owner_id),
                actor=payload.actor,
                decision=payload.decision,
                now=now,
            )
        except ValueError as exc:
            raise AuthorityFailure("hub", "conflict", str(exc), 409) from exc
        authorization = issuer.issue_admission_context(
            actor=payload.actor,
            business_owner_id=payload.decision.target_business_owner_id,
            intent_id=intent.intent_id,
        )
        recovery = await self.hub.get_enrollment_recovery(
            enrollment_id=payload.decision.enrollment_id,
            authorization=authorization,
        )
        self._validate_decision_target(payload, recovery)
        if intent.result is None:
            result = await self.hub.decide_enrollment(
                command=payload.decision,
                command_id=intent.command_id,
                correlation_id=intent.correlation_id,
                authorization=authorization,
            )
            try:
                intent = self.admission_intents.mark_decision_committed(
                    intent_id=intent.intent_id,
                    result=result,
                    now=datetime.now(UTC),
                )
            except ValueError as exc:
                raise AuthorityFailure("hub", "conflict", str(exc), 409) from exc
        recovery = await self.hub.get_enrollment_recovery(
            enrollment_id=payload.decision.enrollment_id,
            authorization=authorization,
        )
        self._validate_committed_decision(payload, intent.result, recovery)
        return AdmissionDecisionWorkflowResult(
            request_id=payload.request_id,
            intent_id=intent.intent_id,
            command_id=intent.command_id,
            checkpoint=intent.checkpoint,
            decision_result=intent.result,
            recovery=recovery,
        )

    def _admission_issuer(self) -> HubAdminCredentialIssuer:
        if self.hub_credentials is None:
            raise AuthorityFailure(
                "hub",
                "configuration",
                "Hub Admission credential issuer is unavailable",
                503,
            )
        return self.hub_credentials

    @staticmethod
    def _validate_decision_target(payload, recovery) -> None:
        proposal = recovery.proposal
        decision = payload.decision
        if (
            proposal.requested_owner_domain_id != decision.target_owner_domain_id
            or proposal.manifest_ref != decision.reviewed_manifest_ref
        ):
            raise AuthorityFailure(
                "hub",
                "conflict",
                "Hub Proposal no longer matches the explicit Decision",
                409,
            )
        if proposal.state == EnrollmentProposalState.PENDING_REVIEW:
            if proposal.proposal_revision != decision.expected_proposal_revision:
                raise AuthorityFailure(
                    "hub", "conflict", "Hub Proposal revision changed", 409
                )
            return
        committed = recovery.approval_decision
        if (
            proposal.state
            not in {
                EnrollmentProposalState.APPROVED_AWAITING_HANDOFF,
                EnrollmentProposalState.GRANT_DELIVERED,
            }
            or committed is None
            or (
                committed.enrollment_id != decision.enrollment_id
                or committed.actor != payload.actor
                or committed.decision != decision.decision
                or committed.target_owner_domain_id != decision.target_owner_domain_id
                or committed.target_business_owner_id
                != decision.target_business_owner_id
                or committed.reviewed_manifest_ref != decision.reviewed_manifest_ref
                or committed.expected_proposal_revision
                != decision.expected_proposal_revision
            )
        ):
            raise AuthorityFailure(
                "hub", "conflict", "Hub Proposal is not decision-recoverable", 409
            )

    @staticmethod
    def _validate_committed_decision(payload, result, recovery) -> None:
        if result is None or recovery.approval_decision is None:
            raise AuthorityFailure(
                "hub", "contract_violation", "Hub omitted the committed Decision", 502
            )
        decision = recovery.approval_decision
        if (
            decision.decision_id != result.decision_id
            or result.decided_by != payload.actor
            or result.decision != payload.decision.decision
            or decision.actor != payload.actor
            or decision.decision != payload.decision.decision
            or decision.target_business_owner_id
            != payload.decision.target_business_owner_id
            or decision.target_owner_domain_id
            != payload.decision.target_owner_domain_id
        ):
            raise AuthorityFailure(
                "hub",
                "contract_violation",
                "Hub recovery Decision changed identity or scope",
                502,
            )

    async def remove_controller_device(
        self,
        *,
        payload: ControllerDeviceRemovalRequest,
        workload_principal_id: str,
        authorization_context: RemovalOwnerAuthorizationContext,
    ) -> DeviceRemovalResult:
        """Create/resume a durable intent and observe independent authorities.

        Nothing here re-decides whether the Claim may be revoked. This workflow
        used to re-read it from Hub first and refuse the Owner outright when the
        two references disagreed or the read simply failed — a projection making
        an authority's decision, which is how a removal died on an unreachable
        read while the Claim it targeted was perfectly revocable. Hub validates
        Owner Domain, business Owner and all three generations on the revoke
        itself and answers ``GENERATION_CONFLICT`` when the target has moved, so
        the exact DeviceRef the Owner authorized is carried straight to it.
        """

        if self.hub_credentials is None:
            raise AuthorityFailure(
                "hub",
                "configuration",
                "Hub Owner credential issuer is unavailable",
                503,
            )
        target_device_ref = authorization_context.target_device_ref
        authorization_context_json = json.dumps(
            authorization_context.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        canonical_authorization_context = authorization_context_json.encode("utf-8")
        authorization_context_sha256 = hashlib.sha256(
            canonical_authorization_context
        ).hexdigest()
        now = datetime.now(UTC)
        try:
            intent = self.removal_intents.get_or_create(
                ingress_request_id=payload.request_id,
                owner_domain_id=str(target_device_ref.owner_domain_id),
                device_ref=target_device_ref,
                actor_controller_id=payload.controller_id,
                workload_principal_id=workload_principal_id,
                controller_reset_epoch=authorization_context.reset_epoch,
                authorization_context_json=authorization_context_json,
                authorization_context_sha256=authorization_context_sha256,
                reason=payload.reason,
                now=now,
            )
        except ValueError as exc:
            raise AuthorityFailure("hub", "conflict", str(exc), 409) from exc
        if intent.intent_id != authorization_context.intent_id:
            raise AuthorityFailure(
                "hub", "conflict", "Removal intent identity changed", 409
            )

        authorization = self.hub_credentials.issue_removal_intent(
            controller_id=payload.controller_id,
            intent_id=intent.intent_id,
            device_ref=intent.device_ref,
        )
        hub = intent.hub_result
        try:
            if hub is None:
                hub = await self.hub.revoke(
                    device_ref=intent.device_ref,
                    reason=intent.reason,
                    command_id=intent.hub_command_id,
                    correlation_id=intent.intent_id,
                    authorization=authorization,
                )
                intent = self.removal_intents.mark_hub_committed(
                    intent_id=intent.intent_id,
                    result=hub,
                    now=datetime.now(UTC),
                )
        except AuthorityFailure as exc:
            return DeviceRemovalResult(
                request_id=payload.request_id,
                intent_id=intent.intent_id,
                device_ref=intent.device_ref,
                outcome="accepted" if exc.retryable else "blocked",
                completed_stage="received",
                recovery=(
                    "retry-forward-same-request-id"
                    if exc.retryable
                    else "operator-action-required"
                ),
                steps=(
                    WorkflowStep(
                        name="hub_revocation",
                        state="failed",
                        request_id=intent.hub_command_id,
                        failure=exc.to_wire(),
                    ),
                ),
                conditions=(
                    RemovalCondition(
                        name="platform_access_revoked",
                        state="false",
                        authority="hub",
                        authority_ref=intent.hub_command_id,
                        observed_at=datetime.now(UTC),
                    ),
                    RemovalCondition(
                        name="mount_removed",
                        state="unknown",
                        authority="kernel",
                        observed_at=datetime.now(UTC),
                    ),
                    RemovalCondition(
                        name="device_erase_acknowledged",
                        state="unknown",
                        authority="device-control",
                        observed_at=datetime.now(UTC),
                    ),
                ),
            )
        steps = [
            WorkflowStep(
                name="hub_revocation",
                state=hub.outcome,
                request_id=intent.hub_command_id,
            )
        ]
        try:
            mount = await self._wait_for_mount_removal(
                owner_id=payload.owner_id, device_id=payload.device_id
            )
        except AuthorityFailure:
            return DeviceRemovalResult(
                request_id=payload.request_id,
                intent_id=intent.intent_id,
                device_ref=intent.device_ref,
                outcome="accepted",
                completed_stage="claim_revoked",
                recovery="retry-forward-same-request-id",
                steps=tuple(steps),
                hub=hub,
                conditions=self._removal_conditions(
                    intent=intent, mount_state="unknown"
                ),
            )
        removed = mount is None or not mount.active
        erase_state, erase_ref = await self._observe_device_erase(
            intent=intent,
            authorization=authorization,
        )
        platform_converged = removed
        return DeviceRemovalResult(
            request_id=payload.request_id,
            intent_id=intent.intent_id,
            device_ref=intent.device_ref,
            outcome="completed" if platform_converged else "accepted",
            completed_stage="converged" if platform_converged else "claim_revoked",
            recovery=(
                "none" if platform_converged else "retry-forward-same-request-id"
            ),
            steps=tuple(steps),
            hub=hub,
            conditions=self._removal_conditions(
                intent=intent,
                mount_state="true" if removed else "false",
                erase_state=erase_state,
                erase_ref=erase_ref,
            ),
        )

    async def _observe_device_erase(self, *, intent, authorization: str):
        event_id = intent.hub_result.event_id
        if event_id is None:
            return "unknown", None
        try:
            operation = await self.hub.get_device_control_operation(
                device_ref=intent.device_ref,
                source_claim_event_id=event_id,
                authorization=authorization,
            )
        except AuthorityFailure:
            return "unknown", event_id
        return (
            "true"
            if operation.state == "acknowledged"
            and operation.terminal_result == "erased"
            else "false",
            operation.operation_id,
        )

    async def _wait_for_mount_removal(self, *, owner_id: str, device_id: str):
        deadline = time.monotonic() + self.removal_observation_timeout_seconds
        while True:
            mount = await self._owner_mount(
                owner_id=owner_id, device_id=device_id, active_only=False
            )
            if mount is None or not mount.active or time.monotonic() >= deadline:
                return mount
            await asyncio.sleep(0.1)

    @staticmethod
    def _removal_conditions(
        *,
        intent,
        mount_state: str,
        erase_state: str = "unknown",
        erase_ref: str | None = None,
    ):
        observed_at = datetime.now(UTC)
        return (
            RemovalCondition(
                name="platform_access_revoked",
                state="true",
                authority="hub",
                authority_ref=(
                    intent.hub_result.event_id or intent.hub_result.command_id
                ),
                observed_at=observed_at,
            ),
            RemovalCondition(
                name="mount_removed",
                state=mount_state,
                authority="kernel",
                observed_at=observed_at,
            ),
            RemovalCondition(
                name="device_erase_acknowledged",
                state=erase_state,
                authority="device-control",
                authority_ref=erase_ref,
                observed_at=observed_at,
            ),
        )

    async def _owner_mount(
        self, *, owner_id: str, device_id: str, active_only: bool = True
    ):
        page = await self.kernel.list_mounts(owner_id=owner_id)
        for mount in page.mounts:
            if mount.device_id == device_id and (mount.active or not active_only):
                return mount
        return None

    async def set_device_companion(
        self, *, payload: ControllerCompanionAttachment
    ) -> KernelMount:
        """Bind a device to one Companion, or to none.

        Kernel owns the mount and validates the Companion against its own
        authority; this only carries the Owner's decision to it, under the
        revision the Owner was looking at.
        """

        if payload.companion_id is None:
            result = await self.kernel.detach(
                owner_id=payload.owner_id,
                device_id=payload.device_id,
                request_id=payload.request_id,
                expected_revision=payload.expected_revision,
            )
        else:
            result = await self.kernel.attach(
                owner_id=payload.owner_id,
                device_id=payload.device_id,
                companion_id=payload.companion_id,
                request_id=payload.request_id,
                expected_revision=payload.expected_revision,
            )
        return result.mount

    async def release_device(
        self,
        *,
        owner_id: str,
        device_id: str,
        request_id: str,
        expected_revision: int,
    ) -> None:
        """Let a device stop answering as anyone.

        The same detach the Owner can ask for by hand, reached by the archive
        workflow so that putting an Eidolon away does not leave a speaker bound
        to something the runtime will refuse to start.
        """

        await self.kernel.detach(
            owner_id=owner_id,
            device_id=device_id,
            request_id=request_id,
            expected_revision=expected_revision,
        )

    async def list_owner_device_mounts(self, owner_id: str) -> KernelMountPage:
        """Return Kernel-owned membership without requiring Hub operator authority.

        This narrow read is consumed by the Controller-authenticated Local API.
        It deliberately excludes pending Hub enrollments and directory metadata:
        those require the separate Device admission authority contract.
        """

        return await self.kernel.list_mounts(owner_id=owner_id)

    @staticmethod
    def capabilities() -> BoundaryCapabilities:
        return BoundaryCapabilities(
            supported=(
                "data.companion-identity.read",
                "data.owner-default-runtime.read",
                "data.owner-workspace.initialize",
                "hub.admission-recovery.read",
                "hub.admission-decision.submit",
                "hub.claim.read",
                "kernel.device-mount.read-write",
                "admin.admission-decision-intent.workflow",
            ),
            unavailable_without_producer_contract=(
                "data.owner-management",
                "data.companion-management",
                "data.persona-genome-management",
                "data.memory-realm-catalog-management",
                "data.face-and-guard-management",
                "global-audit-projection",
                "runtime-presence-telemetry-projection",
            ),
        )
