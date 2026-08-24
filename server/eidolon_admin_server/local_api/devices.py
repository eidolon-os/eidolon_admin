"""Owner-scoped Device membership composed from Kernel mounts and canonical Claims."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol
from urllib.parse import quote

import httpx
from eidolon_sdk.device_foundation.v1 import ClaimPage, ClaimRecord
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..app.control_plane.contracts import (
    ControllerCompanionAttachment,
    KernelMount,
    KernelMountPage,
)


class DeviceInventoryError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 503) -> None:
        super().__init__(message)
        self.status_code = status_code


class LocalCompanionAttachmentRequest(BaseModel):
    """The Owner's choice of which Companion answers through one device.

    One command with two values rather than two commands: `companion_id: null`
    is "nothing answers through it". `expected_revision` is what the phone was
    looking at, so two Controllers changing the same device do not take turns
    without noticing.
    """

    model_config = ConfigDict(extra="forbid")
    contract_version: Literal["1"]
    request_id: str = Field(min_length=1, max_length=96, pattern=r"^[A-Za-z0-9._:-]+$")
    companion_id: str | None = Field(default=None, min_length=1, max_length=64)
    expected_revision: int = Field(ge=1)

    def to_admin(
        self, *, owner_id: str, device_id: str
    ) -> ControllerCompanionAttachment:
        return ControllerCompanionAttachment(
            contract_version="1",
            request_id=self.request_id,
            owner_id=owner_id,
            device_id=device_id,
            companion_id=self.companion_id,
            expected_revision=self.expected_revision,
        )


class AdminOwnerDevicesPort(Protocol):
    async def list_mounts(self, owner_id: str) -> KernelMountPage: ...
    async def set_companion(
        self, *, payload: ControllerCompanionAttachment
    ) -> KernelMount: ...
    async def close(self) -> None: ...


class LocalDeviceMountView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1)
    attached_companion_id: str | None = Field(default=None, min_length=1, max_length=64)
    updated_at: datetime


class LocalDeviceView(BaseModel):
    """Kernel membership plus Hub's canonical Claim, without copied authority."""

    model_config = ConfigDict(extra="forbid")
    claim: ClaimRecord
    mount: LocalDeviceMountView

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

    async def list_mounts(self, owner_id: str) -> KernelMountPage:
        if not self._token:
            raise DeviceInventoryError("Local API Admin service credential is not configured")
        try:
            response = await self._client.get(
                f"{self._base_url}/api/control-plane/v1/owners/{quote(owner_id, safe='')}/device-mounts",
                headers={"Authorization": f"Bearer {self._token}"}, timeout=self._timeout,
            )
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
            raise DeviceInventoryError("Admin Device membership control plane is unavailable") from exc
        if response.status_code != 200:
            raise DeviceInventoryError(
                "Admin Device membership control plane is unavailable",
                status_code=response.status_code if response.status_code in {401, 403, 409} else 503,
            )
        try:
            page = KernelMountPage.model_validate(response.json())
        except (ValueError, TypeError, ValidationError) as exc:
            raise DeviceInventoryError("Admin Device membership response violated its contract") from exc
        if any(mount.owner_id != owner_id for mount in page.mounts):
            raise DeviceInventoryError("Admin Device membership response returned another Owner", status_code=409)
        return page

    async def set_companion(
        self, *, payload: ControllerCompanionAttachment
    ) -> KernelMount:
        if not self._token:
            raise DeviceInventoryError(
                "Local API Admin service credential is not configured"
            )
        try:
            response = await self._client.put(
                f"{self._base_url}/api/control-plane/v1/owners/"
                f"{quote(payload.owner_id, safe='')}/device-mounts/"
                f"{quote(payload.device_id, safe='')}/companion",
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
                "Admin Device Companion attachment did not complete",
                status_code=response.status_code
                if response.status_code in {401, 403, 404, 409}
                else 503,
            )
        try:
            mount = KernelMount.model_validate(response.json())
        except (ValueError, TypeError, ValidationError) as exc:
            raise DeviceInventoryError(
                "Admin Device Companion attachment response violated its contract"
            ) from exc
        if mount.device_id != payload.device_id or mount.owner_id != payload.owner_id:
            raise DeviceInventoryError(
                "Admin Device Companion attachment answered about another device",
                status_code=502,
            )
        return mount

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def owner_device_inventory_view(
    *, mounts: KernelMountPage, bound_owner_id: str, claims: ClaimPage
) -> LocalDeviceInventoryView:
    if any(mount.owner_id != bound_owner_id for mount in mounts.mounts):
        raise DeviceInventoryError("Host Owner scope does not match Kernel Device membership", status_code=409)
    claimed = {item.device_ref.device_instance_id: item for item in claims.items}
    active = tuple(mount for mount in mounts.mounts if mount.active)
    if any(
        mount.device_id not in claimed or claimed[mount.device_id].device_ref != mount.device_ref
        for mount in active
    ):
        raise DeviceInventoryError(
            "Kernel membership and Hub Claim projection do not match", status_code=502
        )
    return LocalDeviceInventoryView(
        devices=tuple(_device_view(mount, claimed[mount.device_id]) for mount in active)
    )


def _device_view(mount: KernelMount, claim: ClaimRecord) -> LocalDeviceView:
    return LocalDeviceView(
        claim=claim,
        mount=LocalDeviceMountView(
            revision=mount.revision,
            attached_companion_id=mount.attached_companion_id,
            updated_at=mount.updated_at,
        ),
    )
