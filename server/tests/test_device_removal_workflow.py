"""Removing a device an Owner can no longer reach.

The device these tests are about is a dead one: a board whose power chip failed,
permanently offline, still holding an active Claim and still bound to a
Companion. Owner membership is the Owner's decision, so the one thing that must
not be able to veto it is the device — and the second thing is a read this
process performs about state some other authority owns.

What they hold:

- **the Owner's intent is durable before any authority is asked.** A removal
  that cannot be resumed is a removal that has to be re-decided;
- **the authority is asked, not re-derived.** Re-reading the Claim here decided
  nothing Hub does not decide again on the revoke, and its failure used to cost
  the Owner the whole operation;
- **a stale generation is refused by the Authority**, and that refusal is a
  definite answer rather than a lost one;
- **the device's local erase is a separate, reported fact**, never a condition
  of the Owner's own removal succeeding.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from eidolon_sdk.device_foundation.v1 import (
    ActorRef,
    DeviceLocalEraseOperationStatus,
    OwnerAuthorizationContext,
    OwnerDomainId,
)

from eidolon_admin_server.app.control_plane.contracts import (
    ControllerDeviceRemovalRequest,
    DeviceRef,
    HubClaimRevocationResult,
    KernelMount,
    KernelMountPage,
)
from eidolon_admin_server.app.control_plane.errors import AuthorityFailure
from eidolon_admin_server.app.control_plane.hub_credentials import (
    HubAdminCredentialIssuer,
)
from eidolon_admin_server.app.control_plane.service import ControlPlaneService
from eidolon_admin_server.lifecycle_workflow.protocol import (
    RemovalOwnerAuthorizationContext,
    removal_intent_id,
)

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
DOMAIN = OwnerDomainId("owner-domain-a")
# The business Owner, which is not the Owner Domain. The fixture used to pass
# the domain here and nothing objected, because the claim set was hand-written
# on both sides and neither looked.
BUSINESS_OWNER = "owner_683f963f54885e868924"
CONTROLLER = "ectrl-0123456789abcdef0123"
DEVICE = "device-instance-cb2f012772ec"
REQUEST = "device-removal-8b0d1f2e"


def _ref(*, claim_generation: int = 2) -> DeviceRef:
    return DeviceRef(
        device_instance_id=DEVICE,
        owner_domain_id=DOMAIN,
        owner_domain_generation=3,
        claim_generation=claim_generation,
        trust_epoch=1,
    )


def _authorization(*, target: DeviceRef | None = None) -> RemovalOwnerAuthorizationContext:
    device_ref = target or _ref()
    return RemovalOwnerAuthorizationContext(
        controller_grant_generation=4,
        reset_epoch=4,
        owner_authorization_context=OwnerAuthorizationContext(
            workload_principal_id="eidolon-lifecycle-workflow",
            actor=ActorRef(
                principal_id=CONTROLLER,
                principal_type="controller",
                owner_domain_id=DOMAIN,
                granted_scopes=("device.read", "device.claim.revoke"),
                authentication_strength="software",
            ),
            authorized_owner_domain_id=DOMAIN,
            scopes=("device.read", "device.claim.revoke"),
            intent_id=removal_intent_id(
                ingress_request_id=REQUEST, owner_domain_id=str(DOMAIN)
            ),
            target_device_ref=device_ref,
            issued_at=NOW,
            expires_at=NOW + timedelta(minutes=5),
        ),
    )


def _payload() -> ControllerDeviceRemovalRequest:
    return ControllerDeviceRemovalRequest(
        contract_version="1",
        request_id=REQUEST,
        owner_id=BUSINESS_OWNER,
        controller_id=CONTROLLER,
        device_id=DEVICE,
        reason="owner-removed",
    )


class Hub:
    """A Hub Admission that revokes, and refuses to be re-derived from."""

    def __init__(self, *, revoke_failure: AuthorityFailure | None = None) -> None:
        self.revoke_failure = revoke_failure
        self.revoked: list[dict] = []
        self.erase_reads: list[str] = []

    async def get_claim(self, **_kwargs):
        raise AssertionError("the removal workflow must not re-derive the Claim")

    async def revoke(self, *, device_ref, reason, command_id, correlation_id, authorization):
        if self.revoke_failure is not None:
            raise self.revoke_failure
        self.revoked.append({"device_ref": device_ref, "command_id": command_id})
        return HubClaimRevocationResult(
            command_id=command_id,
            outcome="committed",
            device_ref=device_ref,
            aggregate_revision=5,
            occurred_at=NOW,
            event_id="admission-event_1f2e3d4c",
        )

    async def get_device_control_operation(
        self, *, device_ref, source_claim_event_id, authorization
    ) -> DeviceLocalEraseOperationStatus:
        self.erase_reads.append(source_claim_event_id)
        return DeviceLocalEraseOperationStatus(
            operation_id="erase_" + "b" * 40,
            request_fingerprint="sha256:" + "c" * 64,
            device_ref=device_ref,
            created_at=NOW,
            deadline=NOW + timedelta(days=7),
            state="pending",
            attempt_count=0,
            terminal_result=None,
        )


class Kernel:
    """A mount that has not converged, because nothing has unmounted it yet."""

    def __init__(self, *, active: bool = True) -> None:
        self.active = active

    async def list_mounts(self, *, owner_id: str) -> KernelMountPage:
        return KernelMountPage(
            mounts=(
                KernelMount(
                    operation="kernel.device-mount",
                    device_id=DEVICE,
                    owner_id=owner_id,
                    device_ref=_ref(),
                    attached_companion_id="c_683f963f",
                    revision=2,
                    created_at=NOW,
                    updated_at=NOW,
                    request_id="mount-request-1",
                    fingerprint="sha256:" + "d" * 64,
                    active=self.active,
                ),
            ),
            operation="kernel.device-mount-page",
            next_cursor=None,
        )


def _service(hub: Hub, *, kernel: Kernel | None = None) -> ControlPlaneService:
    return ControlPlaneService(
        directory=object(),
        data=object(),
        workspace=object(),
        hub=hub,
        kernel=kernel or Kernel(),
        memory=object(),
        activity=object(),
        hub_credentials=HubAdminCredentialIssuer(secret=b"x" * 32, ttl_seconds=60),
    )


def _condition(result, name: str) -> str:
    return next(item.state for item in result.conditions if item.name == name)


async def test_a_dead_device_cannot_veto_its_own_removal() -> None:
    """The device is never asked. Membership is the Owner's fact alone."""

    hub = Hub()
    service = _service(hub)
    result = await service.remove_controller_device(
        payload=_payload(),
        workload_principal_id="eidolon-local-api",
        authorization_context=_authorization(),
    )

    assert [call["device_ref"] for call in hub.revoked] == [_ref()]
    assert _condition(result, "platform_access_revoked") == "true"
    # Reported, not required: the board is dead and will never sign anything.
    assert _condition(result, "device_erase_acknowledged") == "false"
    assert result.outcome != "blocked"


async def test_an_unconfirmed_local_erase_never_holds_the_platform_back() -> None:
    """The platform is done when the platform is done.

    Claim revoked, mount gone, and a device that will never sign an erase ACK
    because its power chip is dead. The Owner's own removal is complete; the one
    thing still open belongs to the device, and is reported as such rather than
    holding the whole operation permanently unfinished.
    """

    hub = Hub()
    service = _service(hub, kernel=Kernel(active=False))

    result = await service.remove_controller_device(
        payload=_payload(),
        workload_principal_id="eidolon-local-api",
        authorization_context=_authorization(),
    )

    assert result.outcome == "completed"
    assert result.completed_stage == "converged"
    assert _condition(result, "mount_removed") == "true"
    assert _condition(result, "device_erase_acknowledged") == "false"


async def test_the_authority_is_asked_rather_than_re_derived() -> None:
    """No read stands between the Owner's decision and the authority that owns it.

    The Claim reference used to be re-read from Hub before the revoke, and any
    failure of that read — an unreachable Hub, a drifted projection — was raised
    as a refusal. So an Owner's removal could die on an observation while the
    Claim it targeted was perfectly revocable: a projection deciding what an
    authority decides, the same defect the Kernel-mount precondition already
    cost once. The DeviceRef the Owner authorized goes straight to the revoke.
    """

    hub = Hub()
    service = _service(hub)

    result = await service.remove_controller_device(
        payload=_payload(),
        workload_principal_id="eidolon-local-api",
        authorization_context=_authorization(),
    )

    assert [call["device_ref"] for call in hub.revoked] == [_ref()]
    assert _condition(result, "platform_access_revoked") == "true"


async def test_the_owner_intent_is_durable_even_when_the_authority_refuses() -> None:
    """A removal that cannot be resumed is one the Owner has to decide again."""

    hub = Hub(
        revoke_failure=AuthorityFailure(
            "hub", "unavailable", "Hub unavailable", 503, retryable=True
        )
    )
    service = _service(hub)

    result = await service.remove_controller_device(
        payload=_payload(),
        workload_principal_id="eidolon-local-api",
        authorization_context=_authorization(),
    )

    assert result.outcome == "accepted"
    assert result.recovery == "retry-forward-same-request-id"
    intent = service.removal_intents.get_or_create(
        ingress_request_id=REQUEST,
        owner_domain_id=str(DOMAIN),
        device_ref=_ref(),
        actor_controller_id=CONTROLLER,
        workload_principal_id="eidolon-local-api",
        controller_reset_epoch=4,
        authorization_context_json=_stored_context(service),
        authorization_context_sha256=_stored_hash(service),
        reason="owner-removed",
        now=NOW,
    )
    assert intent.intent_id == result.intent_id
    assert intent.hub_result is None


async def test_a_stale_generation_is_refused_by_the_authority() -> None:
    """The Authority decides, and its refusal is an answer rather than a loss.

    Hub already re-validates Owner, Claim and all three generations on the
    revoke itself. Letting a projection reach the same verdict first only added
    a second place that could be wrong about it.
    """

    hub = Hub(
        revoke_failure=AuthorityFailure(
            "hub", "conflict", "Claim generation is stale", 409, code="GENERATION_CONFLICT"
        )
    )
    service = _service(hub)

    result = await service.remove_controller_device(
        payload=_payload(),
        workload_principal_id="eidolon-local-api",
        authorization_context=_authorization(),
    )

    assert result.outcome == "blocked"
    assert result.recovery == "operator-action-required"
    assert _condition(result, "platform_access_revoked") == "false"


def _stored_context(service) -> str:
    return next(iter(service.removal_intents._values.values())).authorization_context_json


def _stored_hash(service) -> str:
    return next(iter(service.removal_intents._values.values())).authorization_context_sha256
