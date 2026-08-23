from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import jwt
import pytest
from eidolon_sdk.device_foundation.v1.lifecycle import (
    ActorRef,
    OwnerAuthorizationContext,
)

from eidolon_admin_server.app.control_plane.contracts import (
    ControllerDeviceAdmissionRequest,
    ControllerDeviceRemovalRequest,
    DeviceRef,
    HubClaimRevocationResult,
    HubDeviceControlOperationStatus,
    HubDevice,
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
from eidolon_admin_server.lifecycle_workflow.protocol import (
    RemovalOwnerAuthorizationContext,
    removal_intent_id,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]

_SECRET = b"admin-hub-owner-management-secret-32-bytes"


class _Hub:
    calls: list[dict]

    def __init__(self) -> None:
        self.calls = []
        self.revoke_failure: AuthorityFailure | None = None
        self.control_state = "delivered"

    async def approve(self, **kwargs) -> HubLifecycleStatus:
        self.calls.append(kwargs)
        return HubLifecycleStatus(
            operation="device.lifecycle-status",
            device_id=kwargs["device_id"],
            owner_id=kwargs["owner_id"],
            lifecycle_state="approved",
        )

    async def get_device(self, **kwargs) -> HubDevice:
        self.calls.append(kwargs)
        return _hub_device(
            device_id=kwargs["device_id"], owner_id=kwargs["owner_id"]
        )

    async def revoke(self, **kwargs) -> HubClaimRevocationResult:
        self.calls.append(kwargs)
        if self.revoke_failure:
            raise self.revoke_failure
        return HubClaimRevocationResult(
            operation="device.claim-revocation-result",
            command_id=kwargs["command_id"],
            outcome="committed",
            device_ref=kwargs["device_ref"],
            aggregate_revision=3,
            occurred_at=datetime.now(UTC),
            event_id="claim-event-1",
            lifecycle_state="revoked",
        )

    async def get_device_control_operation(
        self, **kwargs
    ) -> HubDeviceControlOperationStatus:
        self.calls.append(kwargs)
        return _control_operation(
            kwargs["device_ref"], kwargs["event_id"], state=self.control_state
        )

    async def list_devices(self, **kwargs) -> HubDevicePage:
        self.calls.append(kwargs)
        return HubDevicePage.model_validate(
            {
                "operation": "device.directory-page",
                "next_cursor": None,
                "devices": [],
            }
        )


class _LedgerHub:
    """A Hub that keeps its own management idempotency guard.

    The real Hub stores, per device, the last management request ID together
    with a fingerprint of that mutation — and the fingerprint covers the
    calling principal and the requested owner, not just the device. An ID that
    arrives again carrying a different fingerprint is a reuse, not a replay,
    and the Hub refuses it with a conflict that no retry can clear.
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self._ledger: dict[str, tuple[str, str]] = {}

    async def get_device(self, **kwargs) -> HubDevice:
        self.calls.append(kwargs)
        return _hub_device(
            device_id=kwargs["device_id"], owner_id=kwargs["owner_id"]
        )

    async def approve(self, **kwargs) -> HubLifecycleStatus:
        self.calls.append(kwargs)
        self._record(
            operation="device.approve",
            device_id=kwargs["device_id"],
            request_id=kwargs["request_id"],
            values={"owner_id": kwargs["owner_id"]},
            authorization=kwargs["authorization"],
        )
        return HubLifecycleStatus(
            operation="device.lifecycle-status",
            device_id=kwargs["device_id"],
            owner_id=kwargs["owner_id"],
            lifecycle_state="approved",
        )

    async def revoke(self, **kwargs) -> HubClaimRevocationResult:
        self.calls.append(kwargs)
        return HubClaimRevocationResult(
            operation="device.claim-revocation-result",
            command_id=kwargs["command_id"],
            outcome="committed",
            device_ref=kwargs["device_ref"],
            aggregate_revision=3,
            occurred_at=datetime.now(UTC),
            event_id="claim-event-1",
            lifecycle_state="revoked",
        )

    async def get_device_control_operation(
        self, **kwargs
    ) -> HubDeviceControlOperationStatus:
        self.calls.append(kwargs)
        return _control_operation(kwargs["device_ref"], kwargs["event_id"])

    def _record(
        self,
        *,
        operation: str,
        device_id: str,
        request_id: str,
        values: dict[str, str],
        authorization: str,
    ) -> None:
        claims = jwt.decode(
            authorization.removeprefix("Bearer "),
            _SECRET,
            algorithms=["HS256"],
            audience="eidolon-admission",
        )
        document = json.dumps(
            {
                "operation": operation,
                "values": {**values, "principal_id": claims["sub"]},
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        fingerprint = sha256(document.encode()).hexdigest()
        seen = self._ledger.get(device_id)
        if seen is not None and seen[0] == request_id and seen[1] != fingerprint:
            raise AuthorityFailure(
                "hub",
                "conflict",
                "management request_id was reused",
                409,
                409,
                False,
            )
        self._ledger[device_id] = (request_id, fingerprint)


class _Kernel:
    def __init__(self) -> None:
        self.mount_calls: list[dict] = []
        self.attach_calls: list[dict] = []
        self.unmount_calls: list[dict] = []
        self.mount_failure: AuthorityFailure | None = None
        self.mounted: tuple[KernelMount, ...] = ()

    async def list_mounts(self, **_kwargs) -> KernelMountPage:
        return KernelMountPage(
            operation="kernel.device-mount-page",
            next_cursor=None,
            mounts=self.mounted,
        )

    async def unmount(self, **kwargs) -> KernelMutationResult:
        self.unmount_calls.append(kwargs)
        mount = _mount(
            device_id=kwargs["device_id"],
            owner_id=kwargs["owner_id"],
            request_id=kwargs["request_id"],
            revision=kwargs["expected_revision"] + 1,
        )
        return KernelMutationResult(
            operation="kernel.device-mount-mutation-result",
            mount=mount.model_copy(update={"active": False}),
            audit_position=3,
            replayed=False,
        )

    async def mount(self, **kwargs) -> KernelMutationResult:
        if self.mount_failure:
            raise self.mount_failure
        self.mount_calls.append(kwargs)
        return KernelMutationResult(
            operation="kernel.device-mount-mutation-result",
            mount=_mount(
                device_id=kwargs["device_id"],
                owner_id=kwargs["owner_id"],
                request_id=kwargs["request_id"],
            ),
            audit_position=1,
            replayed=len(self.mount_calls) > 1,
        )

    async def attach(self, **kwargs) -> KernelMutationResult:
        self.attach_calls.append(kwargs)
        return KernelMutationResult(
            operation="kernel.device-mount-mutation-result",
            mount=_mount(
                device_id=kwargs["device_id"],
                owner_id=kwargs["owner_id"],
                request_id=kwargs["request_id"],
                companion_id=kwargs["companion_id"],
                revision=2,
            ),
            audit_position=2,
            replayed=len(self.attach_calls) > 1,
        )


def _mount(
    *,
    device_id: str,
    owner_id: str,
    request_id: str,
    companion_id: str | None = None,
    revision: int = 1,
) -> KernelMount:
    now = datetime.now(UTC)
    return KernelMount(
        operation="kernel.device-mount",
        device_id=device_id,
        owner_id=owner_id,
        device_ref=DeviceRef(
            device_instance_id=device_id,
            owner_domain_id=owner_id,
            owner_domain_generation=1,
            claim_generation=1,
            trust_epoch=1,
            accepted_manifest_digest="sha256:" + "a" * 64,
        ),
        attached_companion_id=companion_id,
        revision=revision,
        created_at=now,
        updated_at=now,
        request_id=request_id,
        fingerprint="sha256:" + "0" * 64,
        active=True,
    )


def _hub_device(*, device_id: str, owner_id: str) -> HubDevice:
    return HubDevice.model_validate(
        {
            "operation": "device.directory-entry",
            "device_id": device_id,
            "owner_scope": owner_id,
            "display_name": "Device",
            "device_kind": "generic",
            "manifest": {
                "schema_version": 1,
                "title": "Device",
                "properties": [],
                "actions": [],
                "events": [],
                "media": [],
            },
            "manifest_revision": "sha256:" + "a" * 64,
            "lifecycle_state": "approved",
            "enrolled_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
            "device_ref": {
                "device_instance_id": device_id,
                "owner_domain_id": owner_id,
                "owner_domain_generation": 1,
                "claim_generation": 1,
                "trust_epoch": 1,
                "accepted_manifest_digest": "sha256:" + "a" * 64,
            },
        }
    )


def _control_operation(
    device_ref: DeviceRef, event_id: str, *, state: str = "delivered"
) -> HubDeviceControlOperationStatus:
    now = datetime.now(UTC)
    return HubDeviceControlOperationStatus(
        operation="device-control.operation-status",
        event_id=event_id,
        operation_id=f"channel-revoke:{event_id}",
        operation_type="channel.device-access.revoke",
        device_ref=device_ref,
        state=state,
        attempt_count=0,
        next_attempt_at=now,
        delivered_at=now if state == "delivered" else None,
    )


def _request(**overrides) -> ControllerDeviceAdmissionRequest:
    return ControllerDeviceAdmissionRequest(
        **{
            "contract_version": "1",
            "request_id": "mobile-claim-1",
            "owner_id": "owner-1",
            "controller_id": "ectrl-0123456789abcdefabcd",
            "device_id": "device-1",
            "companion_id": "companion-1",
            **overrides,
        }
    )


def _service(hub: _Hub, kernel: _Kernel) -> ControlPlaneService:
    return ControlPlaneService(
        directory=object(),  # type: ignore[arg-type]
        data=object(),  # type: ignore[arg-type]
        workspace=object(),  # type: ignore[arg-type]
        hub=hub,  # type: ignore[arg-type]
        kernel=kernel,  # type: ignore[arg-type]
        memory=object(),  # type: ignore[arg-type]
        hub_credentials=HubAdminCredentialIssuer(secret=_SECRET),
    )


async def test_controller_admission_mints_admin_credential_and_binds_device() -> None:
    hub, kernel = _Hub(), _Kernel()

    result = await _service(hub, kernel).admit_controller_device(payload=_request())

    assert result.outcome == "completed"
    assert result.completed_stage == "companion_attached"
    assert result.hub is not None
    assert result.hub.device_id == "device-1"
    assert kernel.mount_calls[0]["device_id"] == "device-1"
    assert kernel.attach_calls[0]["device_id"] == "device-1"
    encoded = hub.calls[0]["authorization"].removeprefix("Bearer ")
    claims = jwt.decode(
        encoded, _SECRET, algorithms=["HS256"], audience="eidolon-admission"
    )
    assert "owner_id" not in claims
    assert claims["roles"] == ["hub-admin"]
    assert claims["sub"] == "eidolon-local-api/ectrl-0123456789abcdefabcd"


async def test_controller_admission_retry_reuses_all_deterministic_child_ids() -> None:
    hub, kernel = _Hub(), _Kernel()
    kernel.mount_failure = AuthorityFailure(
        "kernel", "unavailable", "kernel down", 503, retryable=True
    )
    service = _service(hub, kernel)

    first = await service.admit_controller_device(payload=_request())
    second = await service.admit_controller_device(payload=_request())

    assert first.outcome == second.outcome == "retry_required"
    assert first.completed_stage == second.completed_stage == "hub_approved"
    assert hub.calls[0]["request_id"] == hub.calls[1]["request_id"]
    assert hub.calls[0]["request_id"].endswith(":hub-approve")
    assert len(hub.calls[0]["request_id"]) <= 96


async def test_a_second_phone_can_claim_a_device_the_first_phone_already_claimed() -> None:
    # A household can hold more than one phone, and App derives its approval
    # request ID from the Host and the device alone — so both phones send the
    # identical one for the same device, on purpose, so a dropped reply resumes
    # instead of starting a second claim. The Hub keys its management
    # idempotency on that ID *together with* the Controller that called, so the
    # ID Admin derives from it has to carry the Controller too. Deriving it from
    # the device and the mobile ID alone hands the Hub one ID under two
    # different fingerprints, which it refuses forever: the second phone can
    # never claim a device the first one claimed, and no retry clears it.
    hub, kernel = _LedgerHub(), _Kernel()
    service = _service(hub, kernel)  # type: ignore[arg-type]
    stable = "device-approval-2TrNj_OvNtC7u357M62_EaEpZJq2VluBv13i-StzeJo"

    first = await service.admit_controller_device(payload=_request(request_id=stable))
    second = await service.admit_controller_device(
        payload=_request(
            request_id=stable,
            controller_id="ectrl-fedcba98765432100000",
        )
    )

    assert first.outcome == "completed"
    assert second.outcome == "completed"
    assert hub.calls[0]["request_id"] != hub.calls[1]["request_id"]


async def test_a_second_phone_can_remove_a_device_the_first_phone_removed() -> None:
    # A new confirmation creates a new intent, while Hub still transitions the
    # same Claim generation at most once.
    hub, kernel = _LedgerHub(), _Kernel()
    service = _service(hub, kernel)  # type: ignore[arg-type]
    first = await _remove(
        service, _removal_request(request_id="device-removal-one")
    )
    second = await _remove(
        service,
        _removal_request(
            request_id="device-removal-two",
            controller_id="ectrl-fedcba98765432100000",
        ),
    )

    assert first.outcome == "completed"
    assert second.outcome == "completed"
    assert first.intent_id != second.intent_id


async def test_controller_admission_refuses_missing_internal_credential_source() -> None:
    hub, kernel = _Hub(), _Kernel()
    service = ControlPlaneService(
        directory=object(),  # type: ignore[arg-type]
        data=object(),  # type: ignore[arg-type]
        workspace=object(),  # type: ignore[arg-type]
        hub=hub,  # type: ignore[arg-type]
        kernel=kernel,  # type: ignore[arg-type]
        memory=object(),  # type: ignore[arg-type]
    )

    with pytest.raises(AuthorityFailure) as caught:
        await service.admit_controller_device(payload=_request())

    assert caught.value.kind == "configuration"
    assert hub.calls == []
    assert kernel.mount_calls == []


async def test_pending_directory_uses_admin_credential_and_unclaimed_scope() -> None:
    hub, kernel = _Hub(), _Kernel()

    result = await _service(hub, kernel).list_pending_device_enrollments(
        controller_id="ectrl-0123456789abcdefabcd"
    )

    assert result.devices == ()
    assert hub.calls[0]["owner_id"] == "unclaimed"
    assert hub.calls[0]["lifecycle_state"] == "pending-approval"
    encoded = hub.calls[0]["authorization"].removeprefix("Bearer ")
    claims = jwt.decode(
        encoded, _SECRET, algorithms=["HS256"], audience="eidolon-admission"
    )
    assert claims["roles"] == ["hub-admin"]
    assert "owner_id" not in claims


def _removal_request(**overrides) -> ControllerDeviceRemovalRequest:
    return ControllerDeviceRemovalRequest(
        **{
            "contract_version": "1",
            "request_id": "mobile-remove-1",
            "owner_id": "owner-1",
            "controller_id": "ectrl-0123456789abcdefabcd",
            "device_id": "device-1",
            **overrides,
        }
    )


def _removal_context(
    payload: ControllerDeviceRemovalRequest,
) -> RemovalOwnerAuthorizationContext:
    device_ref = DeviceRef(
        device_instance_id=payload.device_id,
        owner_domain_id=payload.owner_id,
        owner_domain_generation=1,
        claim_generation=1,
        trust_epoch=1,
        accepted_manifest_digest="sha256:" + "a" * 64,
    )
    return RemovalOwnerAuthorizationContext(
        controller_grant_generation=0,
        reset_epoch=0,
        owner_authorization_context=OwnerAuthorizationContext(
            workload_principal_id="eidolon-lifecycle-workflow",
            actor=ActorRef(
                principal_id=payload.controller_id,
                principal_type="controller",
                owner_domain_id=payload.owner_id,
                granted_scopes=("device.read", "device.claim.revoke"),
                authentication_strength="software",
            ),
            authorized_owner_domain_id=payload.owner_id,
            scopes=("device.read", "device.claim.revoke"),
            intent_id=removal_intent_id(
                ingress_request_id=payload.request_id,
                owner_domain_id=payload.owner_id,
            ),
            target_device_ref=device_ref,
            issued_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(minutes=1),
        ),
    )


async def _remove(
    service: ControlPlaneService,
    payload: ControllerDeviceRemovalRequest,
):
    return await service.remove_controller_device(
        payload=payload,
        workload_principal_id="eidolon-local-api",
        authorization_context=_removal_context(payload),
    )


async def test_removal_commits_claim_then_waits_for_kernel_event_convergence() -> None:
    hub, kernel = _Hub(), _Kernel()
    kernel.mounted = (
        _mount(device_id="device-1", owner_id="owner-1", request_id="r", revision=3),
    )

    result = await _remove(_service(hub, kernel), _removal_request())

    assert result.outcome == "accepted"
    assert result.completed_stage == "claim_revoked"
    assert [step.name for step in result.steps] == ["hub_revocation"]
    revoke_calls = [call for call in hub.calls if "command_id" in call]
    assert revoke_calls[0]["reason"] == "owner-removed"
    assert kernel.unmount_calls == []
    assert next(c for c in result.conditions if c.name == "mount_removed").state == "false"


async def test_removal_is_idempotent_when_nothing_is_mounted() -> None:
    hub, kernel = _Hub(), _Kernel()

    result = await _remove(_service(hub, kernel), _removal_request())

    assert result.outcome == "completed"
    assert kernel.unmount_calls == []
    assert result.completed_stage == "converged"


async def test_channel_delivery_is_an_independent_completion_condition() -> None:
    hub, kernel = _Hub(), _Kernel()
    hub.control_state = "pending"

    result = await _remove(_service(hub, kernel), _removal_request())

    assert result.outcome == "accepted"
    assert result.completed_stage == "claim_revoked"
    assert next(
        condition
        for condition in result.conditions
        if condition.name == "channel_access_revoked"
    ).state == "false"
    assert next(
        condition
        for condition in result.conditions
        if condition.name == "device_erase_acknowledged"
    ).state == "unknown"


async def test_a_hub_that_refuses_leaves_the_mount_alone() -> None:
    hub, kernel = _Hub(), _Kernel()
    hub.revoke_failure = AuthorityFailure("hub", "not_found", "no such device", 404)
    kernel.mounted = (
        _mount(device_id="device-1", owner_id="owner-1", request_id="r", revision=3),
    )

    result = await _remove(_service(hub, kernel), _removal_request())

    assert result.outcome == "blocked"
    assert result.completed_stage == "received"
    assert kernel.unmount_calls == []
    assert result.steps[0].failure is not None


async def test_repeating_a_removal_reuses_the_same_child_request_ids() -> None:
    hub, kernel = _Hub(), _Kernel()
    kernel.mounted = (
        _mount(device_id="device-1", owner_id="owner-1", request_id="r", revision=3),
    )
    service = _service(hub, kernel)
    payload = _removal_request()
    context = _removal_context(payload)

    first = await service.remove_controller_device(
        payload=payload,
        workload_principal_id="eidolon-local-api",
        authorization_context=context,
    )
    second = await service.remove_controller_device(
        payload=payload,
        workload_principal_id="eidolon-local-api",
        authorization_context=context,
    )

    assert first.steps[0].request_id == second.steps[0].request_id
    assert first.intent_id == second.intent_id
    assert len([call for call in hub.calls if "command_id" in call]) == 1
    assert kernel.unmount_calls == []


async def test_re_admitting_a_removed_device_mounts_at_its_current_revision() -> None:
    # Removal leaves the mount record behind, inactive. Adding the device back
    # has to compare against that revision — mounting at 0 would be rejected,
    # which is exactly what stranded a phone the owner had just removed.
    hub, kernel = _Hub(), _Kernel()
    kernel.mounted = (
        _mount(
            device_id="device-1",
            owner_id="owner-1",
            request_id="r",
            revision=3,
        ).model_copy(update={"active": False}),
    )

    await _service(hub, kernel).admit_controller_device(payload=_request())

    assert kernel.mount_calls[0]["expected_revision"] == 3
    assert kernel.mount_calls[0]["replace_existing"] is False


async def test_a_first_admission_still_mounts_from_nothing() -> None:
    hub, kernel = _Hub(), _Kernel()

    await _service(hub, kernel).admit_controller_device(payload=_request())

    assert kernel.mount_calls[0]["expected_revision"] == 0


async def test_admitting_an_already_mounted_device_touches_nothing() -> None:
    # Every connect re-runs admission. The Kernel refuses to mount over an
    # active mount, so converging on the state — rather than replaying a
    # request — is what makes that safe.
    hub, kernel = _Hub(), _Kernel()
    kernel.mounted = (
        _mount(
            device_id="device-1",
            owner_id="owner-1",
            request_id="r",
            companion_id="companion-1",
            revision=4,
        ),
    )

    result = await _service(hub, kernel).admit_controller_device(payload=_request())

    assert result.outcome == "completed"
    assert result.completed_stage == "companion_attached"
    assert kernel.mount_calls == []
    assert kernel.attach_calls == []


async def test_a_mounted_device_still_gets_its_companion_attached() -> None:
    hub, kernel = _Hub(), _Kernel()
    kernel.mounted = (
        _mount(device_id="device-1", owner_id="owner-1", request_id="r", revision=4),
    )

    result = await _service(hub, kernel).admit_controller_device(payload=_request())

    assert result.outcome == "completed"
    assert kernel.mount_calls == []
    assert kernel.attach_calls[0]["expected_revision"] == 4


async def test_removal_tells_the_hub_whose_device_it_is() -> None:
    """The parameter exists so a call site has to say what it is claiming.

    It nearly did not survive its own introduction: the real client gained a
    required owner_scope and this service kept calling without it, and every
    test passed — because the Hub fakes here take **kwargs and swallow
    anything. A fake that accepts whatever it is given cannot fail a caller
    that stopped agreeing with the thing it stands for.
    """

    hub, kernel = _Hub(), _Kernel()
    service = _service(hub, kernel)  # type: ignore[arg-type]

    payload = ControllerDeviceRemovalRequest(
        contract_version="1",
        request_id="mobile-removal-1",
        owner_id="owner-1",
        controller_id="ectrl-0123456789abcdefabcd",
        device_id="device-1",
        reason="owner-removed",
    )
    await _remove(service, payload)

    exact_query = next(call for call in hub.calls if "owner_id" in call)
    revoke = next(call for call in hub.calls if "device_ref" in call)
    assert exact_query["owner_id"] == "owner-1"
    assert revoke["device_ref"].owner_domain_id == "owner-1"
    claims = jwt.decode(
        revoke["authorization"].removeprefix("Bearer "),
        _SECRET,
        algorithms=["HS256"],
        audience="eidolon-admission",
    )
    assert claims["scopes"] == ["device.claim.revoke"]
    assert claims["target_owner_domain_generation"] == 1
    assert claims["target_claim_generation"] == 1
    assert claims["target_trust_epoch"] == 1
    assert claims["target_manifest_digest"] == "sha256:" + "a" * 64
