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
#:
#: These are the only sentences on this API written for the person rather than
#: for whoever is reading the Host, so they are the only ones in the language
#: that person's App speaks. Everything else here stays English, because the App
#: never shows it: it grades a status code into its own words. That only holds
#: while the two are told apart on the wire, which is what
#: `device_admission_detail` is for — a bare sentence is a diagnostic, and only
#: a tagged reason is meant for a screen.
_REFUSAL_REASONS: dict[str, str] = {
    "conflict": (
        "主机不接受这台设备当前的状态。请刷新列表后重试；若仍被拒绝，"
        "先把这台设备从主机上移除，再重新添加。"
    ),
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
        self,
        message: str,
        *,
        status_code: int = 503,
        reason: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        #: An Owner-facing sentence, set only where a refusal was graded into
        #: one. Everything else raised here is a diagnostic for whoever reads
        #: the Host — it names authorities and contracts, not anything the
        #: person holding the phone can act on — so it stays untagged and App
        #: keeps grading the status code into its own words.
        self.reason = reason


def device_admission_detail(exc: DeviceAdmissionError) -> str | dict[str, str]:
    """What may leave the Host when a device request could not be completed.

    Tagging the graded reason is what keeps "App never shows the diagnostics"
    true: App cannot tell a sentence written for the Owner from one written for
    a developer by looking at it, so the Host says which it is rather than
    leaving App to guess — and guessing wrong in the direction that puts
    contract-violation wording on a screen.
    """

    return {"reason": exc.reason} if exc.reason is not None else str(exc)


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
        # Admin refused with something other than an authority failure, so
        # there is no graded sentence to offer and nothing here is fit to show.
        _LOGGER.warning(
            "Admin Device %s refused with HTTP %s and no authority failure",
            operation,
            response.status_code,
        )
        return DeviceAdmissionError(fallback, status_code=status_code)
    return DeviceAdmissionError(fallback, status_code=status_code, reason=reason)


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


#: What an act came to, in the only terms that change what a person does next.
#:
#: ``done`` — it finished; there is nothing to decide.
#: ``unfinished`` — it did not finish, and asking again can still finish it.
#: ``refused`` — it did not finish, and asking again will not change that.
#:
#: Three fields used to say this between them: a ``state`` that mixed how far
#: the act got with whether it had ended, a ``completed_stage`` naming an
#: internal authority hand-off, and a ``retryable`` flag. A screen had to
#: reassemble a decision out of all three, and every screen reassembled it
#: slightly differently. The distinction that actually matters is the one this
#: contract keeps everywhere else: a Host that got partway has not decided
#: anything, and a Host that refused has.
ActOutcome = Literal["done", "unfinished", "refused"]


class LocalDeviceAdmissionProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Literal["local.device-admission-progress"] = (
        "local.device-admission-progress"
    )
    contract_version: Literal["1"] = "1"
    request_id: str = Field(min_length=1, max_length=128)
    device_id: str = Field(min_length=1, max_length=128)
    owner_id: str = Field(min_length=1, max_length=64)
    outcome: ActOutcome
    #: How far it got, for someone diagnosing it. Never the basis of what a
    #: screen tells a person to do — that is what ``outcome`` is for.
    stopped_after: Literal[
        "hub-approved",
        "kernel-mounted",
        "companion-attached",
    ]
    companion_id: str | None = Field(default=None, min_length=1, max_length=64)


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
    outcome: ActOutcome
    #: ``hub-revoked`` with ``unfinished`` is the state worth reading twice:
    #: the grant is gone, so the device is already off, and what is left to
    #: retry is the unmount.
    stopped_after: Literal["hub-revoked", "kernel-unmounted"]


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
    return LocalDeviceRemovalProgress(
        request_id=result.request_id,
        device_id=device_id,
        owner_id=owner_id,
        outcome=_act_outcome(result.outcome),
        stopped_after=stage,
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
    if result.outcome == "completed" and (
        companion_id is not None and result.completed_stage != "companion_attached"
    ):
        raise DeviceAdmissionError(
            "Admin Device admission omitted the requested Companion attachment",
            status_code=502,
        )
    return LocalDeviceAdmissionProgress(
        request_id=result.request_id,
        device_id=hub.device_id,
        owner_id=owner_id,
        outcome=_act_outcome(result.outcome),
        stopped_after=stage,
        companion_id=companion_id,
    )


def _act_outcome(outcome: str) -> ActOutcome:
    """What Admin's three internal outcomes mean to the person who asked.

    ``retry_required`` is the whole reason this distinction exists: an act
    that stopped partway has decided nothing, and the same request will carry
    it further. Anything else that is not completion is a refusal, and
    repeating it only produces the same refusal again.
    """

    if outcome == "completed":
        return "done"
    if outcome == "retry_required":
        return "unfinished"
    return "refused"


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
