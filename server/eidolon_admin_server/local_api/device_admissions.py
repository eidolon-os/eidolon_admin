"""Controller-scoped Device admission facade over Admin's forward workflow."""

from __future__ import annotations

from typing import Literal, Protocol
from urllib.parse import quote

import httpx
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
)

from ..app.control_plane.contracts import (
    DeviceAdmissionResult,
    DevicePairingAdmissionRequest,
)
from .config import VerifiedHubOnboardingTarget


_BASE64URL_CHARS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
)


class DeviceAdmissionError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 503) -> None:
        super().__init__(message)
        self.status_code = status_code


class LocalDeviceOnboardingTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Literal["local.device-onboarding-target"] = (
        "local.device-onboarding-target"
    )
    contract_version: Literal["1"] = "1"
    hub_id: str = Field(min_length=1, max_length=128)
    descriptor_uri: str = Field(max_length=2048, pattern=r"^https://")
    tls_spki_fingerprint: str = Field(pattern=r"^sha256:[A-Za-z0-9_-]{43}$")

    @classmethod
    def from_verified(
        cls,
        target: VerifiedHubOnboardingTarget,
    ) -> LocalDeviceOnboardingTarget:
        return cls(
            hub_id=target.hub_id,
            descriptor_uri=target.descriptor_uri,
            tls_spki_fingerprint=target.tls_spki_fingerprint,
        )


class LocalDeviceAdmissionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"]
    request_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    hub_id: str = Field(min_length=1, max_length=128)
    descriptor_uri: str = Field(max_length=2048, pattern=r"^https://")
    enrollment_id: str = Field(pattern=r"^enrollment_[A-Za-z0-9_-]{24}$")
    pairing_secret: SecretStr = Field(
        min_length=43,
        max_length=43,
        repr=False,
    )
    companion_id: str | None = Field(default=None, min_length=1, max_length=64)

    @field_validator("pairing_secret")
    @classmethod
    def _pairing_secret_is_base64url(cls, value: SecretStr) -> SecretStr:
        secret = value.get_secret_value()
        if any(character not in _BASE64URL_CHARS for character in secret):
            raise ValueError("pairing_secret must be canonical unpadded base64url")
        return value

    def verify_target(self, target: VerifiedHubOnboardingTarget) -> None:
        if self.hub_id != target.hub_id or self.descriptor_uri != target.descriptor_uri:
            raise DeviceAdmissionError(
                "Device admission target does not match this Host installation",
                status_code=409,
            )

    def to_admin(
        self,
        *,
        owner_id: str,
        controller_id: str,
    ) -> DevicePairingAdmissionRequest:
        return DevicePairingAdmissionRequest(
            contract_version="1",
            request_id=self.request_id,
            owner_id=owner_id,
            controller_id=controller_id,
            enrollment_id=self.enrollment_id,
            pairing_secret=self.pairing_secret,
            companion_id=self.companion_id,
        )


class LocalDeviceAdmissionProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Literal["local.device-admission-progress"] = (
        "local.device-admission-progress"
    )
    contract_version: Literal["1"] = "1"
    setup_id: str = Field(min_length=1, max_length=128)
    request_id: str = Field(min_length=1, max_length=128)
    device_id: str = Field(min_length=1, max_length=128)
    enrollment_id: str = Field(min_length=1, max_length=128)
    owner_id: str = Field(min_length=1, max_length=64)
    state: Literal["approved", "binding", "ready", "failed"]
    completed_stage: Literal[
        "hub-approved",
        "kernel-mounted",
        "companion-attached",
    ]
    companion_id: str | None = Field(default=None, min_length=1, max_length=64)
    retryable: bool


class AdminDeviceAdmissionPort(Protocol):
    async def claim(
        self,
        *,
        setup_id: str,
        payload: DevicePairingAdmissionRequest,
    ) -> DeviceAdmissionResult: ...

    async def close(self) -> None: ...


class AdminDeviceAdmissionClient:
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

    async def claim(
        self,
        *,
        setup_id: str,
        payload: DevicePairingAdmissionRequest,
    ) -> DeviceAdmissionResult:
        if not self._token:
            raise DeviceAdmissionError(
                "Local API Admin service credential is not configured"
            )
        wire = payload.model_dump(mode="json", exclude={"pairing_secret"})
        wire["pairing_secret"] = payload.pairing_secret.get_secret_value()
        try:
            response = await self._client.put(
                f"{self._base_url}/api/control-plane/v1/local-device-admissions/"
                f"{quote(setup_id, safe='')}",
                headers={"Authorization": f"Bearer {self._token}"},
                json=wire,
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
            status_code = response.status_code if response.status_code in {
                403,
                404,
                409,
                422,
                502,
                503,
            } else 503
            raise DeviceAdmissionError(
                "Admin Device admission did not complete the requested transition",
                status_code=status_code,
            )
        try:
            result = DeviceAdmissionResult.model_validate(response.json())
        except (ValueError, TypeError, ValidationError) as exc:
            raise DeviceAdmissionError(
                "Admin Device admission response violated its contract"
            ) from exc
        if result.request_id != payload.request_id:
            raise DeviceAdmissionError(
                "Admin Device admission response returned another request",
                status_code=409,
            )
        return result

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def device_admission_progress(
    *,
    setup_id: str,
    enrollment_id: str,
    owner_id: str,
    companion_id: str | None,
    result: DeviceAdmissionResult,
) -> LocalDeviceAdmissionProgress:
    hub = result.hub
    if hub is None or hub.owner_id != owner_id or hub.lifecycle_state != "approved":
        raise DeviceAdmissionError(
            "Admin Device admission did not confirm the Host Owner scope",
            status_code=502,
        )
    if result.mount is not None and (
        result.mount.device_id != hub.device_id
        or result.mount.owner_id != owner_id
    ):
        raise DeviceAdmissionError(
            "Admin Device admission crossed its Hub or Owner identity",
            status_code=502,
        )
    stage = {
        "hub_approved": "hub-approved",
        "kernel_mounted": "kernel-mounted",
        "companion_attached": "companion-attached",
    }.get(result.completed_stage)
    if stage is None:
        raise DeviceAdmissionError(
            "Admin Device admission returned an unsupported stage",
            status_code=502,
        )
    if result.outcome == "completed":
        if companion_id is not None and result.completed_stage != "companion_attached":
            raise DeviceAdmissionError(
                "Admin Device admission omitted the requested Companion attachment",
                status_code=502,
            )
        state: Literal["approved", "binding", "ready", "failed"] = "ready"
    elif result.outcome == "retry_required":
        state = "binding" if result.completed_stage == "kernel_mounted" else "approved"
    else:
        state = "failed"
    return LocalDeviceAdmissionProgress(
        setup_id=setup_id,
        request_id=result.request_id,
        device_id=hub.device_id,
        enrollment_id=enrollment_id,
        owner_id=owner_id,
        state=state,
        completed_stage=stage,
        companion_id=companion_id,
        retryable=result.outcome == "retry_required",
    )
