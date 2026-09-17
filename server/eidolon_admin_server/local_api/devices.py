"""Owner-scoped Device membership composed from Kernel mounts and canonical Claims."""

from __future__ import annotations

from datetime import datetime
from collections.abc import Mapping
from typing import Literal, Protocol
from urllib.parse import quote

import httpx
from eidolon_sdk.biz.presentation import DeviceOutputPolicy, OutputSelection
from eidolon_sdk.biz.presentation.device import (
    DeviceOutputConfiguration,
    SetDeviceOutputPolicy,
)
from eidolon_sdk.device_foundation.v1 import (
    BusinessOwnerId,
    ClaimPage,
    ClaimRecord,
    DeviceRef,
    OwnerDomainId,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..app.control_plane.contracts import (
    ControllerBodyAssignment,
    ControllerDeviceOutputPolicyQuery,
    ControllerDeviceOutputPolicyUpdate,
    KernelBodyEndpoint,
    KernelBodyEndpointPage,
    assignment_revision_of,
)
from .device_admissions import admission_actor


class DeviceInventoryError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 503) -> None:
        super().__init__(message)
        self.status_code = status_code


class AdminOwnerDevicesPort(Protocol):
    async def list_body_endpoints(self, owner_id: str) -> KernelBodyEndpointPage: ...
    async def set_companion(
        self, *, payload: ControllerBodyAssignment
    ) -> KernelBodyEndpoint: ...
    async def read_output_configuration(
        self, *, payload: ControllerDeviceOutputPolicyQuery
    ) -> DeviceOutputConfiguration: ...
    async def set_output_policy(
        self, *, payload: ControllerDeviceOutputPolicyUpdate
    ) -> DeviceOutputPolicy: ...
    async def close(self) -> None: ...


def output_configuration_query(
    *,
    controller_id: str,
    owner_domain_id: OwnerDomainId,
    business_owner_id: BusinessOwnerId,
    device_ref: DeviceRef,
) -> ControllerDeviceOutputPolicyQuery:
    return ControllerDeviceOutputPolicyQuery(
        contract_version="1",
        actor=admission_actor(
            controller_id=controller_id, owner_domain_id=owner_domain_id
        ),
        business_owner_id=business_owner_id,
        device_ref=device_ref,
    )


def output_policy_decision(
    *,
    controller_id: str,
    owner_domain_id: OwnerDomainId,
    business_owner_id: BusinessOwnerId,
    device_ref: DeviceRef,
    expected_revision: int,
    allowed: OutputSelection,
) -> ControllerDeviceOutputPolicyUpdate:
    return ControllerDeviceOutputPolicyUpdate(
        contract_version="1",
        actor=admission_actor(
            controller_id=controller_id, owner_domain_id=owner_domain_id
        ),
        business_owner_id=business_owner_id,
        policy=SetDeviceOutputPolicy(
            device_ref=device_ref,
            expected_revision=expected_revision,
            allowed=allowed,
        ),
    )


class LocalDeviceBodyView(BaseModel):
    """The one Body a device has, and what is assigned to it.

    ``assignment_revision`` is what a change has to carry, and it is the *Body's*
    revision rather than the mount's: pointing a speaker at a different Eidolon
    and re-claiming that speaker are two facts now, and making them share a
    compare-and-swap token is what used to make one silently discard the other.
    """

    model_config = ConfigDict(extra="forbid")
    body_endpoint_id: str = Field(min_length=1, max_length=128)
    mount_revision: int = Field(ge=1)
    assignment_revision: int = Field(ge=0)
    answering_companion_id: str | None = Field(default=None, min_length=1, max_length=64)
    #: Why it is pointing where it is. ``None`` for a Body nobody has decided
    #: about, which is a different thing from having been cleared.
    selection_provenance: str | None = Field(default=None, max_length=32)
    updated_at: datetime


class LocalDeviceView(BaseModel):
    """Kernel Body plus Hub's canonical Claim, without copied authority.

    ``outputs`` is the Authority's own answer carried whole: what this device
    declared it can present, and what its Owner has allowed. A ``policy`` of
    ``None`` is the one thing a Companion device cannot serve without and the
    only thing nobody but the Owner can supply, so it is shown beside the
    device rather than left to be inferred from a channel that never arrives.
    """

    model_config = ConfigDict(extra="forbid")
    claim: ClaimRecord
    body: LocalDeviceBodyView
    outputs: DeviceOutputConfiguration

    @property
    def device_id(self) -> str:
        return self.claim.device_ref.device_instance_id


class LocalDeviceInventoryView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contract_version: str = "1"
    coverage: str = "active-kernel-mounts-with-owner-scoped-hub-claims"
    devices: tuple[LocalDeviceView, ...] = Field(default=(), max_length=100)


class AdminOwnerDevicesClient:
    def __init__(
        self, *, base_url: str, service_token: str, timeout_seconds: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = service_token.strip()
        self._timeout = timeout_seconds
        self._client = client or httpx.AsyncClient(trust_env=False)
        self._owns_client = client is None

    async def list_body_endpoints(self, owner_id: str) -> KernelBodyEndpointPage:
        if not self._token:
            raise DeviceInventoryError("Local API Admin service credential is not configured")
        try:
            response = await self._client.get(
                f"{self._base_url}/api/control-plane/v1/owners/"
                f"{quote(owner_id, safe='')}/body-endpoints",
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=self._timeout,
            )
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
            raise DeviceInventoryError("Admin Device membership control plane is unavailable") from exc
        if response.status_code != 200:
            raise DeviceInventoryError(
                "Admin Device membership control plane is unavailable",
                status_code=response.status_code if response.status_code in {401, 403, 409} else 503,
            )
        try:
            page = KernelBodyEndpointPage.model_validate(response.json())
        except (ValueError, TypeError, ValidationError) as exc:
            raise DeviceInventoryError("Admin Device membership response violated its contract") from exc
        if any(endpoint.owner_id != owner_id for endpoint in page.endpoints):
            raise DeviceInventoryError("Admin Device membership response returned another Owner", status_code=409)
        return page

    async def set_companion(
        self, *, payload: ControllerBodyAssignment
    ) -> KernelBodyEndpoint:
        if not self._token:
            raise DeviceInventoryError(
                "Local API Admin service credential is not configured"
            )
        try:
            response = await self._client.put(
                f"{self._base_url}/api/control-plane/v1/owners/"
                f"{quote(payload.owner_id, safe='')}/body-endpoints/"
                f"{quote(payload.device_id, safe='')}/assignment",
                headers={"Authorization": f"Bearer {self._token}"},
                json=payload.model_dump(mode="json"),
                timeout=self._timeout,
            )
        except (
            httpx.TimeoutException,
            httpx.NetworkError,
            httpx.RemoteProtocolError,
        ) as exc:
            raise DeviceInventoryError(
                "Admin Device membership control plane is unavailable"
            ) from exc
        if response.status_code != 200:
            raise DeviceInventoryError(
                "Admin Device Companion assignment did not complete",
                status_code=response.status_code
                if response.status_code in {401, 403, 404, 409}
                else 503,
            )
        try:
            endpoint = KernelBodyEndpoint.model_validate(response.json())
        except (ValueError, TypeError, ValidationError) as exc:
            raise DeviceInventoryError(
                "Admin Device Companion assignment response violated its contract"
            ) from exc
        if (
            endpoint.device_id != payload.device_id
            or endpoint.owner_id != payload.owner_id
        ):
            raise DeviceInventoryError(
                "Admin Device Companion assignment answered about another device",
                status_code=502,
            )
        return endpoint

    async def _device_scoped(self, *, verb: str, suffix: str, owner_id: str,
                            device_id: str, payload, model, operation: str):
        """One device-scoped call to the Admin control plane, and its refusals.

        The two output-policy calls differ only in verb, suffix and answer
        type, so they share the transport rather than each carrying its own
        copy of the status mapping — the one place a read and a write silently
        disagree about what 409 means.
        """

        if not self._token:
            raise DeviceInventoryError(
                "Local API Admin service credential is not configured"
            )
        try:
            response = await self._client.request(
                verb,
                f"{self._base_url}/api/control-plane/v1/owners/"
                f"{quote(owner_id, safe='')}/devices/"
                f"{quote(device_id, safe='')}/{suffix}",
                headers={"Authorization": f"Bearer {self._token}"},
                json=payload.model_dump(mode="json"),
                timeout=self._timeout,
            )
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
            raise DeviceInventoryError(f"Admin Device {operation} is unavailable") from exc
        if response.status_code not in {200, 202}:
            raise DeviceInventoryError(
                f"Admin Device {operation} did not complete",
                status_code=response.status_code
                if response.status_code in {401, 403, 404, 409}
                else 503,
            )
        try:
            return model.model_validate(response.json())
        except (ValueError, TypeError, ValidationError) as exc:
            raise DeviceInventoryError(
                f"Admin Device {operation} response violated its contract"
            ) from exc

    async def read_output_configuration(
        self, *, payload: ControllerDeviceOutputPolicyQuery
    ) -> DeviceOutputConfiguration:
        configuration = await self._device_scoped(
            verb="POST",
            suffix="output-policy-queries",
            owner_id=str(payload.business_owner_id),
            device_id=payload.device_ref.device_instance_id,
            payload=payload,
            model=DeviceOutputConfiguration,
            operation="output configuration",
        )
        if configuration.device_ref != payload.device_ref:
            raise DeviceInventoryError(
                "Admin Device output configuration answered about another device",
                status_code=502,
            )
        return configuration

    async def set_output_policy(
        self, *, payload: ControllerDeviceOutputPolicyUpdate
    ) -> DeviceOutputPolicy:
        policy = await self._device_scoped(
            verb="PUT",
            suffix="output-policy",
            owner_id=str(payload.business_owner_id),
            device_id=payload.policy.device_ref.device_instance_id,
            payload=payload,
            model=DeviceOutputPolicy,
            operation="output policy decision",
        )
        if policy.allowed != payload.policy.allowed:
            raise DeviceInventoryError(
                "Admin Device output policy stored another decision", status_code=502
            )
        return policy

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def owner_device_inventory_view(
    *,
    endpoints: KernelBodyEndpointPage,
    bound_owner_id: str,
    claims: ClaimPage,
    outputs: Mapping[str, DeviceOutputConfiguration],
) -> LocalDeviceInventoryView:
    if any(endpoint.owner_id != bound_owner_id for endpoint in endpoints.endpoints):
        raise DeviceInventoryError(
            "Host Owner scope does not match Kernel Device membership", status_code=409
        )
    claimed = {item.device_ref.device_instance_id: item for item in claims.items}
    present = tuple(endpoint for endpoint in endpoints.endpoints if endpoint.present)
    if any(
        endpoint.device_id not in claimed
        or claimed[endpoint.device_id].device_ref != endpoint.device_ref
        for endpoint in present
    ):
        raise DeviceInventoryError(
            "Kernel membership and Hub Claim projection do not match", status_code=502
        )
    if any(endpoint.device_id not in outputs for endpoint in present):
        raise DeviceInventoryError(
            "Device output configuration is missing for a claimed device", status_code=502
        )
    return LocalDeviceInventoryView(
        devices=tuple(
            _device_view(endpoint, claimed[endpoint.device_id], outputs[endpoint.device_id])
            for endpoint in present
        )
    )


def _device_view(
    endpoint: KernelBodyEndpoint,
    claim: ClaimRecord,
    outputs: DeviceOutputConfiguration,
) -> LocalDeviceView:
    assignment = endpoint.assignment
    return LocalDeviceView(
        claim=claim,
        outputs=outputs,
        body=LocalDeviceBodyView(
            body_endpoint_id=endpoint.body_endpoint_id,
            mount_revision=endpoint.mount_revision,
            assignment_revision=assignment_revision_of(endpoint),
            # What the authority says is in force, not what the spec names: a
            # Body keeps its assignment when its device goes away so it can come
            # back to the same Eidolon, and only the status knows the difference.
            answering_companion_id=(
                None if assignment is None else assignment.status.effective_companion_id
            ),
            selection_provenance=(
                None if assignment is None else assignment.selection_provenance
            ),
            updated_at=(claim.updated_at if assignment is None else assignment.updated_at),
        ),
    )
