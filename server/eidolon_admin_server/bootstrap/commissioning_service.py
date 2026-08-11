"""Transport-independent use cases executed inside an authenticated setup session."""

from __future__ import annotations

import base64
import hashlib
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import load_der_public_key

from .domain import (
    is_usable_setup_code,
    BootstrapOperation,
    BootstrapOperationState,
    BootstrapOperationType,
    ControllerGrant,
    ControllerRole,
    NetworkState,
)
from .ports import (
    BootstrapStateConflict,
    BootstrapStateStore,
    NetworkChangeRequest,
    NetworkProvisioning,
    NetworkProvisioningError,
)


_CONTROLLER_ID = re.compile(r"^ectrl-[0-9a-f]{20}$")

logger = logging.getLogger("eidolon.bootstrap.commissioning")


class CommissioningRequestRejected(RuntimeError):
    """A setup request is invalid or not allowed in the current state."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class CommissioningAuthorization:
    """Secret-free authorization passed beyond the TLS session boundary."""

    session_id: str
    secret_hash: str


@dataclass(frozen=True, slots=True)
class ControllerAuthorization:
    grant: ControllerGrant


CommissioningAccess = CommissioningAuthorization | ControllerAuthorization


def _now() -> datetime:
    return datetime.now(UTC)


def _timestamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _decode_base64url(value: str, *, field: str) -> bytes:
    if not isinstance(value, str) or not value:
        raise CommissioningRequestRejected("invalid_request", f"{field} is required")
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, TypeError) as exc:
        raise CommissioningRequestRejected(
            "invalid_request", f"{field} must be unpadded base64url"
        ) from exc
    canonical = base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii")
    if canonical != value:
        raise CommissioningRequestRejected(
            "invalid_request", f"{field} must use canonical base64url"
        )
    return decoded


def _controller_grant(
    payload: dict[str, Any], *, reset_epoch: int, created_at: str
) -> ControllerGrant:
    encoded_key = payload.get("public_key")
    der = _decode_base64url(encoded_key, field="public_key")
    try:
        public_key = load_der_public_key(der)
    except ValueError as exc:
        raise CommissioningRequestRejected(
            "invalid_controller_key", "Controller public key is not valid DER"
        ) from exc
    if not isinstance(public_key, ec.EllipticCurvePublicKey) or not isinstance(
        public_key.curve, ec.SECP256R1
    ):
        raise CommissioningRequestRejected(
            "invalid_controller_key", "Controller key must be P-256"
        )
    digest = hashlib.sha256(der).hexdigest()
    controller_id = f"ectrl-{digest[:20]}"
    supplied_id = payload.get("controller_id")
    if supplied_id is not None and (
        not isinstance(supplied_id, str)
        or not _CONTROLLER_ID.fullmatch(supplied_id)
        or supplied_id != controller_id
    ):
        raise CommissioningRequestRejected(
            "invalid_controller_key", "Controller ID does not match its public key"
        )
    display_name = payload.get("display_name")
    if not isinstance(display_name, str) or not display_name.strip():
        raise CommissioningRequestRejected(
            "invalid_request", "Controller display_name is required"
        )
    display_name = display_name.strip()
    if len(display_name) > 80:
        raise CommissioningRequestRejected(
            "invalid_request", "Controller display_name is too long"
        )
    platform = payload.get("platform")
    if platform not in {"android", "ios"}:
        raise CommissioningRequestRejected(
            "invalid_request", "Controller platform is unsupported"
        )
    return ControllerGrant(
        controller_id=controller_id,
        public_key=encoded_key,
        public_key_fingerprint=f"sha256:{digest}",
        role=ControllerRole.HOST_ADMIN,
        display_name=display_name,
        platform=platform,
        reset_epoch=reset_epoch,
        created_at=created_at,
    )


class CommissioningService:
    """Initial network and claim state machine, independent of BLE and D-Bus."""

    def __init__(
        self,
        *,
        store: BootstrapStateStore,
        network: NetworkProvisioning,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        self._store = store
        self._network = network
        self._clock = clock

    def authorize(self, *, session_id: str, secret: str) -> CommissioningAuthorization:
        if not isinstance(session_id, str) or not session_id:
            raise CommissioningRequestRejected(
                "commissioning_denied", "Commissioning session is unavailable"
            )
        if not isinstance(secret, str) or not is_usable_setup_code(secret):
            raise CommissioningRequestRejected(
                "commissioning_denied", "Commissioning session is unavailable"
            )
        secret_hash = hashlib.sha256(secret.encode("utf-8")).hexdigest()
        try:
            self._store.authorize_commissioning_session(
                session_id=session_id,
                secret_hash=secret_hash,
                now=_timestamp(self._clock()),
            )
        except BootstrapStateConflict as exc:
            raise CommissioningRequestRejected(
                "commissioning_denied", "Commissioning session is unavailable"
            ) from exc
        return CommissioningAuthorization(session_id, secret_hash)

    def authorize_controller(self, controller_id: str) -> ControllerAuthorization:
        if not isinstance(controller_id, str) or not _CONTROLLER_ID.fullmatch(
            controller_id
        ):
            raise CommissioningRequestRejected(
                "controller_denied", "Controller is not authorized for this Host"
            )
        grant = self._store.get_controller(controller_id)
        state = self._store.get_state()
        if (
            grant is None
            or grant.revoked_at is not None
            or grant.reset_epoch != state.reset_epoch
            or grant.role is not ControllerRole.HOST_ADMIN
        ):
            raise CommissioningRequestRejected(
                "controller_denied", "Controller is not authorized for this Host"
            )
        return ControllerAuthorization(grant)

    def status(self, authorization: CommissioningAccess) -> dict[str, Any]:
        self._require_authorized(authorization)
        return {"state": self._store.get_state().to_dict()}

    async def scan_networks(self, authorization: CommissioningAccess) -> dict[str, Any]:
        self._require_authorized(authorization)
        current = await self._network.get_state()
        access_points = await self._network.scan()
        unique = {}
        for item in access_points:
            previous = unique.get(item.ssid)
            if previous is None or item.signal > previous.signal:
                unique[item.ssid] = item
        ordered = sorted(unique.values(), key=lambda item: (-item.signal, item.ssid))
        return {
            "current_network": {
                "state": current.state.value,
                "ssid": current.current_ssid,
            },
            "networks": [
                {"ssid": item.ssid, "signal": item.signal, "secured": item.secured}
                for item in ordered
            ]
        }

    async def configure_network(
        self,
        authorization: CommissioningAccess,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self._require_authorized(authorization)
        operation_id = self._operation_id(payload.get("operation_id"))
        ssid = self._ssid(payload.get("ssid"))
        passphrase = payload.get("passphrase")
        if passphrase is not None and not isinstance(passphrase, str):
            raise CommissioningRequestRejected(
                "invalid_network", "Wi-Fi passphrase must be a string"
            )
        if passphrase is not None and not 8 <= len(passphrase) <= 63:
            raise CommissioningRequestRejected(
                "invalid_network", "Wi-Fi passphrase must contain 8 to 63 characters"
            )
        hidden = payload.get("hidden", False)
        if not isinstance(hidden, bool):
            raise CommissioningRequestRejected(
                "invalid_network", "hidden must be a boolean"
            )
        existing = self._store.get_operation(operation_id)
        if existing is not None:
            if existing.target != ssid:
                raise CommissioningRequestRejected(
                    "operation_conflict", "operation_id is already in use"
                )
            return {"operation": existing.to_dict()}
        state = self._store.get_state()
        if state.claim_state.value == "unclaimed":
            if not isinstance(authorization, CommissioningAuthorization):
                raise CommissioningRequestRejected(
                    "commissioning_denied", "Initial setup credential is required"
                )
            operation_type = BootstrapOperationType.INITIAL_NETWORK
        else:
            if not isinstance(authorization, ControllerAuthorization):
                raise CommissioningRequestRejected(
                    "controller_denied", "Host Admin authorization is required"
                )
            operation_type = BootstrapOperationType.CHANGE_NETWORK
        now = _timestamp(self._clock())
        operation = BootstrapOperation(
            operation_id=operation_id,
            operation_type=operation_type,
            state=BootstrapOperationState.PENDING,
            target=ssid,
            reset_epoch=state.reset_epoch,
            created_at=now,
            updated_at=now,
        )
        operation_created = False
        previous_network_state = state.network_state
        try:
            self._store.create_operation(operation)
            operation_created = True
            self._store.update_operation(
                operation_id,
                state=BootstrapOperationState.RUNNING,
                network_state=NetworkState.STAGING,
                updated_at=now,
            )
            await self._network.begin_change(
                NetworkChangeRequest(
                    operation_id=operation_id,
                    ssid=ssid,
                    passphrase=passphrase,
                    hidden=hidden,
                )
            )
            operation = self._store.update_operation(
                operation_id,
                state=BootstrapOperationState.WAITING_CONFIRMATION,
                network_state=NetworkState.STAGING,
                updated_at=_timestamp(self._clock()),
            )
        except (BootstrapStateConflict, NetworkProvisioningError) as exc:
            logger.warning(
                "Wi-Fi staging failed operation_id=%s ssid=%r reason=%s",
                operation_id,
                ssid,
                exc,
            )
            if operation_created:
                fallback = (
                    NetworkState.CONNECTED
                    if previous_network_state is NetworkState.CONNECTED
                    else NetworkState.DEGRADED
                )
                self._store.update_operation(
                    operation_id,
                    state=BootstrapOperationState.FAILED,
                    network_state=fallback,
                    updated_at=_timestamp(self._clock()),
                    error_code="network_stage_failed",
                )
            raise CommissioningRequestRejected(
                "network_stage_failed", "The Host could not stage this Wi-Fi network"
            ) from exc
        return {"operation": operation.to_dict()}

    async def confirm_network(
        self,
        authorization: CommissioningAccess,
        operation_id: str,
    ) -> dict[str, Any]:
        self._require_authorized(authorization)
        operation_id = self._operation_id(operation_id)
        operation = self._store.get_operation(operation_id)
        if operation is None:
            raise CommissioningRequestRejected(
                "operation_not_found", "Network operation does not exist"
            )
        if operation.state is BootstrapOperationState.SUCCEEDED:
            return {"operation": operation.to_dict()}
        if operation.state is not BootstrapOperationState.WAITING_CONFIRMATION:
            raise CommissioningRequestRejected(
                "operation_conflict", "Network operation cannot be confirmed"
            )
        try:
            await self._network.confirm(operation_id)
        except NetworkProvisioningError as exc:
            raise CommissioningRequestRejected(
                "network_confirm_failed", "The staged Wi-Fi network is not connected"
            ) from exc
        operation = self._store.update_operation(
            operation_id,
            state=BootstrapOperationState.SUCCEEDED,
            network_state=NetworkState.CONNECTED,
            updated_at=_timestamp(self._clock()),
        )
        return {"operation": operation.to_dict()}

    async def rollback_network(
        self,
        authorization: CommissioningAccess,
        operation_id: str,
    ) -> dict[str, Any]:
        self._require_authorized(authorization)
        operation_id = self._operation_id(operation_id)
        operation = self._store.get_operation(operation_id)
        if operation is None:
            raise CommissioningRequestRejected(
                "operation_not_found", "Network operation does not exist"
            )
        if operation.state is BootstrapOperationState.FAILED:
            return {"operation": operation.to_dict()}
        try:
            self._store.update_operation(
                operation_id,
                state=BootstrapOperationState.COMPENSATING,
                network_state=NetworkState.ROLLING_BACK,
                updated_at=_timestamp(self._clock()),
            )
            snapshot = await self._network.rollback(operation_id)
        except (BootstrapStateConflict, NetworkProvisioningError) as exc:
            raise CommissioningRequestRejected(
                "network_rollback_failed", "The Wi-Fi rollback did not complete"
            ) from exc
        operation = self._store.update_operation(
            operation_id,
            state=BootstrapOperationState.FAILED,
            network_state=snapshot.state,
            updated_at=_timestamp(self._clock()),
            error_code="cancelled",
        )
        return {"operation": operation.to_dict()}

    def claim_controller(
        self,
        authorization: CommissioningAuthorization,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        state = self._store.get_state()
        now = _timestamp(self._clock())
        grant = _controller_grant(
            payload, reset_epoch=state.reset_epoch, created_at=now
        )
        try:
            grant = self._store.claim_controller(
                session_id=authorization.session_id,
                secret_hash=authorization.secret_hash,
                grant=grant,
                now=now,
            )
        except BootstrapStateConflict as exc:
            reason = str(exc)
            code = (
                "already_claimed"
                if reason == "host is already claimed"
                else "commissioning_denied"
                if reason == "commissioning session is unavailable"
                else "operation_conflict"
            )
            raise CommissioningRequestRejected(code, reason) from exc
        return {
            "controller": grant.to_dict(),
            "state": self._store.get_state().to_dict(),
        }

    def _require_authorized(self, authorization: CommissioningAccess) -> None:
        if isinstance(authorization, ControllerAuthorization):
            current = self._store.get_controller(authorization.grant.controller_id)
            state = self._store.get_state()
            if (
                current is None
                or current.revoked_at is not None
                or current.public_key != authorization.grant.public_key
                or current.reset_epoch != state.reset_epoch
            ):
                raise CommissioningRequestRejected(
                    "controller_denied", "Controller is not authorized for this Host"
                )
            return
        try:
            self._store.authorize_commissioning_session(
                session_id=authorization.session_id,
                secret_hash=authorization.secret_hash,
                now=_timestamp(self._clock()),
            )
        except BootstrapStateConflict as exc:
            raise CommissioningRequestRejected(
                "commissioning_denied", "Commissioning session is unavailable"
            ) from exc

    @staticmethod
    def _operation_id(value: Any) -> str:
        if not isinstance(value, str):
            raise CommissioningRequestRejected(
                "invalid_request", "operation_id is required"
            )
        try:
            parsed = uuid.UUID(value)
        except ValueError as exc:
            raise CommissioningRequestRejected(
                "invalid_request", "operation_id must be a UUID"
            ) from exc
        if str(parsed) != value:
            raise CommissioningRequestRejected(
                "invalid_request", "operation_id must use canonical UUID encoding"
            )
        return value

    @staticmethod
    def _ssid(value: Any) -> str:
        if not isinstance(value, str) or not value:
            raise CommissioningRequestRejected("invalid_network", "SSID is required")
        if not 1 <= len(value.encode("utf-8")) <= 32:
            raise CommissioningRequestRejected(
                "invalid_network", "SSID must contain 1 to 32 UTF-8 bytes"
            )
        return value
