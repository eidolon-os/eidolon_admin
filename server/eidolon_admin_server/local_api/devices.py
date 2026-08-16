"""Sanitized owner-scoped Device membership for the Mobile product API."""

from __future__ import annotations

from datetime import datetime
from collections.abc import Mapping
from typing import Literal, Protocol
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ..app.control_plane.contracts import (
    HubDevice,
    KernelMount,
    KernelMountPage,
    OwnerInventory,
)


class DeviceInventoryError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 503) -> None:
        super().__init__(message)
        self.status_code = status_code


class AdminOwnerDevicesPort(Protocol):
    async def list_mounts(self, owner_id: str) -> KernelMountPage: ...

    async def list_inventory(
        self,
        owner_id: str,
        controller_id: str,
    ) -> OwnerInventory: ...

    async def rename(
        self,
        owner_id: str,
        controller_id: str,
        device_id: str,
        display_name: str,
    ) -> HubDevice: ...

    async def close(self) -> None: ...


class LocalDeviceMountView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: int = Field(ge=1)
    attached_companion_id: str | None = Field(default=None, min_length=1, max_length=64)
    updated_at: datetime


class LocalDeviceView(BaseModel):
    """One device an Owner has, as they would recognise it.

    Two authorities answer here and neither is asked to speak for the other:
    Kernel says this is mounted to this Owner, and Hub says what it is called
    and what kind of thing it is. Membership was all this carried, so a device
    a person had adopted appeared as …4a52f354 — while the queue it came from
    had been calling it Box-3 all along.

    When Hub cannot be reached the name is simply absent. It is not filled in
    with an identifier: an identifier is what someone falls back to when
    nobody will tell them what a thing is.
    """

    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(min_length=1, max_length=128)
    display_name: str = Field(default="", max_length=128)
    device_kind: str = Field(default="", max_length=96)
    admission_state: Literal["mounted", "ready"]
    mount: LocalDeviceMountView


class LocalDeviceRenameCommand(BaseModel):
    """What to call a device, as the person typed it.

    Blank is refused here rather than two services away: the answer is the
    same, and this is the boundary the person is talking to.
    """

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"] = "1"
    display_name: str = Field(min_length=1, max_length=128)

    @field_validator("display_name")
    @classmethod
    def _must_be_a_name(cls, value: str) -> str:
        name = value.strip()
        if not name:
            raise ValueError("display_name cannot be blank")
        return name


class LocalDeviceInventoryView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"] = "1"
    coverage: Literal["mounted-devices"] = "mounted-devices"
    devices: tuple[LocalDeviceView, ...] = Field(default=(), max_length=100)


class AdminOwnerDevicesClient:
    def __init__(
        self,
        *,
        base_url: str,
        service_token: str,
        timeout_seconds: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = service_token.strip()
        self._timeout = timeout_seconds
        self._client = client or httpx.AsyncClient(trust_env=False)
        self._owns_client = client is None

    async def list_mounts(self, owner_id: str) -> KernelMountPage:
        if not self._token:
            raise DeviceInventoryError(
                "Local API Admin service credential is not configured"
            )
        url = (
            f"{self._base_url}/api/control-plane/v1/owners/"
            f"{quote(owner_id, safe='')}/device-mounts"
        )
        try:
            response = await self._client.get(
                url,
                headers={"Authorization": f"Bearer {self._token}"},
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
        if response.status_code in {401, 403}:
            raise DeviceInventoryError(
                "Local API is not authorized to read Device membership"
            )
        if response.status_code != 200:
            raise DeviceInventoryError(
                "Admin Device membership control plane is unavailable"
            )
        try:
            page = KernelMountPage.model_validate(response.json())
        except (ValueError, TypeError, ValidationError) as exc:
            raise DeviceInventoryError(
                "Admin Device membership response violated its contract"
            ) from exc
        if any(mount.owner_id != owner_id for mount in page.mounts):
            raise DeviceInventoryError(
                "Admin Device membership response returned another Owner",
                status_code=409,
            )
        return page

    async def list_inventory(
        self,
        owner_id: str,
        controller_id: str,
    ) -> OwnerInventory:
        """Both authorities' answer about this Owner's devices."""

        if not self._token:
            raise DeviceInventoryError(
                "Local API Admin service credential is not configured"
            )
        url = (
            f"{self._base_url}/api/control-plane/v1/owners/"
            f"{quote(owner_id, safe='')}/device-inventory/"
            f"{quote(controller_id, safe='')}"
        )
        try:
            response = await self._client.get(
                url,
                headers={"Authorization": f"Bearer {self._token}"},
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
                "Admin Device membership control plane is unavailable",
                status_code=response.status_code
                if response.status_code in {401, 403, 409}
                else 503,
            )
        try:
            return OwnerInventory.model_validate(response.json())
        except (ValueError, TypeError, ValidationError) as exc:
            raise DeviceInventoryError(
                "Admin Device membership response violated its contract"
            ) from exc

    async def rename(
        self,
        owner_id: str,
        controller_id: str,
        device_id: str,
        display_name: str,
    ) -> HubDevice:
        if not self._token:
            raise DeviceInventoryError(
                "Local API Admin service credential is not configured"
            )
        url = (
            f"{self._base_url}/api/control-plane/v1/owners/"
            f"{quote(owner_id, safe='')}/devices/{quote(device_id, safe='')}"
            f"/name/{quote(controller_id, safe='')}"
        )
        try:
            response = await self._client.patch(
                url,
                headers={"Authorization": f"Bearer {self._token}"},
                json={"display_name": display_name},
                timeout=self._timeout,
            )
        except (
            httpx.TimeoutException,
            httpx.NetworkError,
            httpx.RemoteProtocolError,
        ) as exc:
            raise DeviceInventoryError(
                "Admin Device directory is unavailable"
            ) from exc
        if response.status_code == 404:
            raise DeviceInventoryError("Device does not exist", status_code=404)
        if response.status_code == 422:
            raise DeviceInventoryError("Device name was rejected", status_code=422)
        if response.status_code != 200:
            raise DeviceInventoryError("Admin Device directory is unavailable")
        try:
            return HubDevice.model_validate(response.json())
        except (ValueError, TypeError, ValidationError) as exc:
            raise DeviceInventoryError(
                "Admin Device rename response violated its contract"
            ) from exc

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def owner_device_inventory_view(
    *,
    mounts: KernelMountPage,
    bound_owner_id: str,
    directory: Mapping[str, HubDevice] | None = None,
) -> LocalDeviceInventoryView:
    if any(mount.owner_id != bound_owner_id for mount in mounts.mounts):
        raise DeviceInventoryError(
            "Host Owner scope does not match Kernel Device membership",
            status_code=409,
        )
    # Kernel keeps the mount record of a removed device, inactive, because that
    # record carries the revision the next admission has to swap against. The
    # Owner is not being told about a revision; they are being told what is
    # mounted. An inactive mount is a device this Owner removed, so it belongs
    # to neither this view nor its stated coverage.
    known = directory or {}
    return LocalDeviceInventoryView(
        devices=tuple(
            _device_view(mount, known.get(mount.device_id))
            for mount in mounts.mounts
            if mount.active
        )
    )


def _device_view(mount: KernelMount, entry: HubDevice | None) -> LocalDeviceView:
    return LocalDeviceView(
        device_id=mount.device_id,
        display_name=entry.display_name if entry else "",
        device_kind=entry.device_kind if entry else "",
        admission_state=(
            "ready" if mount.attached_companion_id is not None else "mounted"
        ),
        mount=LocalDeviceMountView(
            revision=mount.revision,
            attached_companion_id=mount.attached_companion_id,
            updated_at=mount.updated_at,
        ),
    )
