"""Controller-scoped Device admission facade over Admin's forward workflow."""

from __future__ import annotations

import logging
from typing import Literal, Protocol
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..app.control_plane.contracts import (
    ControllerDeviceAdmissionRequest,
    ControllerDeviceRemovalRequest,
    DeviceAdmissionResult,
    DeviceRemovalResult,
    HubDevicePage,
)
from .config import VerifiedHubOnboardingTarget

_LOGGER = logging.getLogger(__name__)

#: What each kind of authority refusal means for the person holding the phone.
#:
#: Admin reports which authority refused and what kind of refusal it was. Those
#: kinds are a published vocabulary, so they can be graded once here into
#: something the Owner can act on. What is deliberately not passed on is the
#: authority's own sentence: it names internal request identifiers and
#: authority internals that belong in the Host log, not on a screen.
_REFUSAL_REASONS: dict[str, str] = {
    "conflict": (
        "the Host refused this device in its current state; refresh the list "
        "and retry, and take the device off the Host first if it is refused again"
    ),
    "not_found": "the Host no longer holds this device",
    "unauthorized": "the Host no longer authorizes this Controller for devices",
    "forbidden": "the Host no longer authorizes this Controller for devices",
    "invalid_request": "the Host rejected the contents of this request",
    "unavailable": "a Host device authority is temporarily unavailable",
    "configuration": "a Host device authority is not fully configured",
    "upstream_failure": "a Host device authority failed to answer",
    "contract_violation": "a Host device authority answered outside its contract",
}


class DeviceAdmissionError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 503) -> None:
        super().__init__(message)
        self.status_code = status_code


def _refusal(
    response: httpx.Response,
    *,
    operation: str,
    fallback: str,
    status_code: int,
) -> DeviceAdmissionError:
    """Carry forward *why* Admin refused, instead of only that it refused.

    The Host knows the reason — an authority told it, in words. Answering every
    non-200 with one generic sentence discards that, and leaves the Owner
    refreshing a list that will never change and a developer with a status code.
    So the authority's own words go to the Host log, and the graded reason for
    the refusal travels on.
    """

    authority, kind, words = _authority_failure(response)
    if words is not None:
        _LOGGER.warning(
            "Admin Device %s refused by %s authority (%s): %s",
            operation,
            authority or "an unnamed",
            kind or "unknown kind",
            words,
        )
    reason = _REFUSAL_REASONS.get(kind or "")
    if reason is None:
        return DeviceAdmissionError(fallback, status_code=status_code)
    return DeviceAdmissionError(reason, status_code=status_code)


def _authority_failure(
    response: httpx.Response,
) -> tuple[str | None, str | None, str | None]:
    """Read Admin's authority failure envelope, tolerating anything else."""

    try:
        document = response.json()
    except ValueError:
        return None, None, None
    detail = document.get("detail") if isinstance(document, dict) else None
    if not isinstance(detail, dict):
        return None, None, None

    def _text(key: str) -> str | None:
        value = detail.get(key)
        return value if isinstance(value, str) and value else None

    return _text("authority"), _text("kind"), _text("detail")


class LocalDeviceOnboardingTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Literal["local.device-onboarding-target"] = (
        "local.device-onboarding-target"
    )
    contract_version: Literal["1"] = "1"
    hub_id: str = Field(min_length=1, max_length=128)
    descriptor_uri: str = Field(max_length=2048, pattern=r"^https://")
    tls_spki_fingerprint: str = Field(pattern=r"^sha256:[A-Za-z0-9_-]{43}$")
    #: The Host's own certificate, for a Controller to hand to a device it is
    #: setting up. A device cannot pin a fingerprint it has no way to obtain,
    #: and no public authority can vouch for a Host, so the certificate travels
    #: with the Owner rather than being fetched off the network by the device.
    hub_certificate: str = Field(
        min_length=1,
        max_length=8192,
        pattern=r"^-----BEGIN CERTIFICATE-----",
    )

    @classmethod
    def from_verified(
        cls,
        target: VerifiedHubOnboardingTarget,
    ) -> LocalDeviceOnboardingTarget:
        return cls(
            hub_id=target.hub_id,
            descriptor_uri=target.descriptor_uri,
            tls_spki_fingerprint=target.tls_spki_fingerprint,
            hub_certificate=target.tls_certificate_path.read_text(encoding="utf-8"),
        )


class LocalDeviceApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"]
    request_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    companion_id: str | None = Field(default=None, min_length=1, max_length=64)

    def to_admin(
        self,
        *,
        device_id: str,
        owner_id: str,
        controller_id: str,
    ) -> ControllerDeviceAdmissionRequest:
        return ControllerDeviceAdmissionRequest(
            contract_version="1",
            request_id=self.request_id,
            owner_id=owner_id,
            controller_id=controller_id,
            device_id=device_id,
            companion_id=self.companion_id,
        )


class LocalPendingDeviceEnrollment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(min_length=1, max_length=128)
    display_name: str = Field(max_length=128)
    device_kind: str = Field(min_length=1, max_length=96)
    enrolled_at: str


class LocalPendingDeviceEnrollmentPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Literal["local.pending-device-enrollments"] = (
        "local.pending-device-enrollments"
    )
    contract_version: Literal["1"] = "1"
    devices: tuple[LocalPendingDeviceEnrollment, ...] = ()


class LocalDeviceAdmissionProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Literal["local.device-admission-progress"] = (
        "local.device-admission-progress"
    )
    contract_version: Literal["1"] = "1"
    request_id: str = Field(min_length=1, max_length=128)
    device_id: str = Field(min_length=1, max_length=128)
    owner_id: str = Field(min_length=1, max_length=64)
    state: Literal["approved", "binding", "ready", "failed"]
    completed_stage: Literal[
        "hub-approved",
        "kernel-mounted",
        "companion-attached",
    ]
    companion_id: str | None = Field(default=None, min_length=1, max_length=64)
    retryable: bool


class LocalDeviceRemovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"]
    request_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )

    def to_admin(
        self,
        *,
        device_id: str,
        owner_id: str,
        controller_id: str,
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

    operation: Literal["local.device-removal-progress"] = "local.device-removal-progress"
    contract_version: Literal["1"] = "1"
    request_id: str = Field(min_length=1, max_length=128)
    device_id: str = Field(min_length=1, max_length=128)
    owner_id: str = Field(min_length=1, max_length=64)
    state: Literal["revoked", "removed", "failed"]
    completed_stage: Literal["hub-revoked", "kernel-unmounted"]
    retryable: bool


class AdminDeviceAdmissionPort(Protocol):
    async def list_pending(
        self,
        *,
        controller_id: str,
    ) -> HubDevicePage: ...

    async def claim(
        self,
        *,
        payload: ControllerDeviceAdmissionRequest,
    ) -> DeviceAdmissionResult: ...

    async def remove(
        self,
        *,
        payload: ControllerDeviceRemovalRequest,
    ) -> DeviceRemovalResult: ...

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

    async def list_pending(self, *, controller_id: str) -> HubDevicePage:
        if not self._token:
            raise DeviceAdmissionError(
                "Local API Admin service credential is not configured"
            )
        try:
            response = await self._client.get(
                f"{self._base_url}/api/control-plane/v1/"
                f"pending-device-enrollments/{quote(controller_id, safe='')}",
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=self._timeout,
            )
        except (
            httpx.TimeoutException,
            httpx.NetworkError,
            httpx.RemoteProtocolError,
        ) as exc:
            raise DeviceAdmissionError(
                "Admin Device enrollment directory is unavailable"
            ) from exc
        if response.status_code != 200:
            raise DeviceAdmissionError(
                "Admin Device enrollment directory is unavailable",
                status_code=response.status_code if response.status_code in {403, 503} else 503,
            )
        try:
            return HubDevicePage.model_validate(response.json())
        except (ValueError, TypeError, ValidationError) as exc:
            raise DeviceAdmissionError(
                "Admin Device enrollment directory violated its contract"
            ) from exc

    async def claim(
        self,
        *,
        payload: ControllerDeviceAdmissionRequest,
    ) -> DeviceAdmissionResult:
        if not self._token:
            raise DeviceAdmissionError(
                "Local API Admin service credential is not configured"
            )
        wire = payload.model_dump(mode="json")
        try:
            response = await self._client.put(
                f"{self._base_url}/api/control-plane/v1/local-device-admissions/"
                f"{quote(payload.device_id, safe='')}",
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
            raise _refusal(
                response,
                operation="admission",
                fallback=(
                    "Admin Device admission did not complete the requested transition"
                ),
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

    async def remove(
        self,
        *,
        payload: ControllerDeviceRemovalRequest,
    ) -> DeviceRemovalResult:
        if not self._token:
            raise DeviceAdmissionError(
                "Local API Admin service credential is not configured"
            )
        try:
            response = await self._client.put(
                f"{self._base_url}/api/control-plane/v1/local-device-removals/"
                f"{quote(payload.device_id, safe='')}",
                headers={"Authorization": f"Bearer {self._token}"},
                json=payload.model_dump(mode="json"),
                timeout=self._timeout,
            )
        except (
            httpx.TimeoutException,
            httpx.NetworkError,
            httpx.RemoteProtocolError,
        ) as exc:
            raise DeviceAdmissionError(
                "Admin Device removal control plane is unavailable"
            ) from exc
        if response.status_code != 200:
            status_code = (
                response.status_code
                if response.status_code in {403, 404, 409, 422, 502, 503}
                else 503
            )
            raise _refusal(
                response,
                operation="removal",
                fallback=(
                    "Admin Device removal did not complete the requested transition"
                ),
                status_code=status_code,
            )
        try:
            result = DeviceRemovalResult.model_validate(response.json())
        except (ValueError, TypeError, ValidationError) as exc:
            raise DeviceAdmissionError(
                "Admin Device removal response violated its contract"
            ) from exc
        if result.request_id != payload.request_id:
            raise DeviceAdmissionError(
                "Admin Device removal response returned another request",
                status_code=409,
            )
        return result

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def device_removal_progress(
    *,
    owner_id: str,
    device_id: str,
    result: DeviceRemovalResult,
) -> LocalDeviceRemovalProgress:
    hub = result.hub
    if hub is not None and (
        hub.device_id != device_id or hub.lifecycle_state != "revoked"
    ):
        raise DeviceAdmissionError(
            "Admin Device removal did not confirm the requested device",
            status_code=502,
        )
    stage = {
        "received": "hub-revoked",
        "hub_revoked": "hub-revoked",
        "kernel_unmounted": "kernel-unmounted",
    }.get(result.completed_stage)
    if stage is None:
        raise DeviceAdmissionError(
            "Admin Device removal returned an unsupported stage",
            status_code=502,
        )
    if result.outcome == "completed":
        state: Literal["revoked", "removed", "failed"] = "removed"
    elif result.outcome == "retry_required":
        # The grant is already gone whenever the Hub step committed, so the
        # phone is off either way; what is left to retry is the mount.
        state = "revoked" if result.completed_stage == "hub_revoked" else "failed"
    else:
        state = "failed"
    return LocalDeviceRemovalProgress(
        request_id=result.request_id,
        device_id=device_id,
        owner_id=owner_id,
        state=state,
        completed_stage=stage,
        retryable=result.outcome == "retry_required",
    )


def device_admission_progress(
    *,
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
        request_id=result.request_id,
        device_id=hub.device_id,
        owner_id=owner_id,
        state=state,
        completed_stage=stage,
        companion_id=companion_id,
        retryable=result.outcome == "retry_required",
    )


def pending_device_enrollment_page(
    page: HubDevicePage,
) -> LocalPendingDeviceEnrollmentPage:
    devices = tuple(
        LocalPendingDeviceEnrollment(
            device_id=device.device_id,
            display_name=device.display_name,
            device_kind=device.device_kind,
            enrolled_at=device.enrolled_at.isoformat(),
        )
        for device in page.devices
        if device.owner_scope == "unclaimed"
        and device.lifecycle_state == "pending-approval"
    )
    if len(devices) != len(page.devices):
        raise DeviceAdmissionError(
            "Admin Device enrollment directory crossed the pending scope",
            status_code=502,
        )
    return LocalPendingDeviceEnrollmentPage(devices=devices)
