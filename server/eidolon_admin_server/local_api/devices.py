"""Sanitized owner-scoped Device membership for the Mobile product API."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..app.control_plane.contracts import KernelMount, KernelMountPage


class DeviceInventoryError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 503) -> None:
        super().__init__(message)
        self.status_code = status_code


class AdminOwnerDevicesPort(Protocol):
    async def list_mounts(self, owner_id: str) -> KernelMountPage: ...

    async def close(self) -> None: ...


class LocalDeviceMountView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: Literal["active", "inactive"]
    revision: int = Field(ge=1)
    attached_companion_id: str | None = Field(default=None, min_length=1, max_length=64)
    updated_at: datetime


class LocalDeviceView(BaseModel):
    """Kernel-confirmed membership; directory identity is intentionally absent."""

    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(min_length=1, max_length=128)
    admission_state: Literal["mounted", "ready", "inactive"]
    mount: LocalDeviceMountView


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

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def owner_device_inventory_view(
    *,
    mounts: KernelMountPage,
    bound_owner_id: str,
) -> LocalDeviceInventoryView:
    if any(mount.owner_id != bound_owner_id for mount in mounts.mounts):
        raise DeviceInventoryError(
            "Host Owner scope does not match Kernel Device membership",
            status_code=409,
        )
    return LocalDeviceInventoryView(
        devices=tuple(_device_view(mount) for mount in mounts.mounts)
    )


def _device_view(mount: KernelMount) -> LocalDeviceView:
    state: Literal["mounted", "ready", "inactive"]
    if not mount.active:
        state = "inactive"
    elif mount.attached_companion_id is not None:
        state = "ready"
    else:
        state = "mounted"
    return LocalDeviceView(
        device_id=mount.device_id,
        admission_state=state,
        mount=LocalDeviceMountView(
            state="active" if mount.active else "inactive",
            revision=mount.revision,
            attached_companion_id=mount.attached_companion_id,
            updated_at=mount.updated_at,
        ),
    )
