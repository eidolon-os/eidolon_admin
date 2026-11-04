"""Transport-independent use cases executed inside an authenticated setup session."""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import load_der_public_key

from . import pake
from .domain import (
    BootstrapOperation,
    BootstrapOperationState,
    BootstrapOperationType,
    ControllerGrant,
    ControllerRole,
    NetworkState,
)
from .guess_budget import GuessBudget, GuessRefused
from .ports import (
    BootstrapStateConflict,
    BootstrapStateStore,
    NetworkChangeRequest,
    NetworkProvisioning,
    NetworkProvisioningError,
)


_CONTROLLER_ID = re.compile(r"^ectrl-[0-9a-f]{20}$")
#: How long the Host holds its half of a PAKE between start and confirm.
PAKE_HANDSHAKE_SECONDS = 30.0
#: How many half-finished handshakes are held at once; the oldest goes first.
#: Each one cost a guess to start, so the budget bounds how fast they arrive.
PAKE_MAX_HANDSHAKES = 16

logger = logging.getLogger("eidolon.bootstrap.commissioning")


class CommissioningRequestRejected(RuntimeError):
    """A setup request is invalid or not allowed in the current state."""

    def __init__(
        self, code: str, message: str, *, retry_after_ms: int | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        #: Set on ``rate_limited`` only: when the next attempt would be taken.
        self.retry_after_ms = retry_after_ms


@dataclass(frozen=True, slots=True)
class CommissioningAuthorization:
    """Secret-free authorization passed beyond the TLS session boundary."""

    session_id: str
    secret_hash: str
    #: Set when the code was proven by PAKE: the controller key that proof was
    #: bound to, which is then the only key this authorization can claim with.
    controller_key_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class _Handshake:
    session_id: str
    secret_hash: str
    transport: str
    exchange: pake.ServerExchange
    context: bytes
    controller_key_sha256: str
    expires_at: float


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


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _p256_public_key_der(encoded_key: Any, *, field: str = "public_key") -> bytes:
    der = _decode_base64url(encoded_key, field=field)
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
    return der


def _controller_key_sha256(der: bytes) -> str:
    return _base64url(hashlib.sha256(der).digest())


def _controller_grant(
    payload: dict[str, Any], *, reset_epoch: int, created_at: str
) -> ControllerGrant:
    encoded_key = payload.get("public_key")
    der = _p256_public_key_der(encoded_key)
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
        host_context: Callable[[], dict[str, str]],
        on_claimed: Callable[[], None] | None = None,
        budget: GuessBudget | None = None,
        entropy: Callable[[int], bytes] = secrets.token_bytes,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store = store
        self._network = network
        self._clock = clock
        #: Paces guesses at the code (guess_budget.py). None paces nothing,
        #: which is what a test of this service on its own wants; the Host
        #: composition always passes one.
        self._budget = budget
        #: The Host's long-term and TLS public keys, which the PAKE binds into
        #: its transcript. Required: the PAKE is the only way a Setup code is
        #: accepted, so a service without them could authorize no one.
        self._host_context = host_context
        self._entropy = entropy
        self._monotonic = monotonic
        self._handshakes: dict[str, _Handshake] = {}
        #: Called after a claim has been committed, so the composition that
        #: knows this Host's claim-window policy can act on it. Kept as a
        #: callback rather than a settings object: whether a window should be
        #: standing is not a fact this service reasons about, and giving it the
        #: factory code to re-mint would put a secret in the one place that
        #: only ever needed a hash.
        self._on_claimed = on_claimed

    def pake_start(
        self,
        payload: dict[str, Any],
        *,
        transport: str,
        source: str | None = None,
    ) -> dict[str, Any]:
        """The Host's first PAKE message: salt, B, and the context both proofs
        will be bound to. Spends one guess, because this is where a guess is
        committed to — the code is fixed by the A the phone sends next."""

        if payload.get("suite") != pake.SUITE:
            raise CommissioningRequestRejected(
                "unsupported_suite", f"This Host offers {pake.SUITE} only"
            )
        session_id = payload.get("commissioning_id")
        if not isinstance(session_id, str) or not session_id:
            raise CommissioningRequestRejected(
                "commissioning_denied", "Commissioning session is unavailable"
            )
        key_der = _p256_public_key_der(
            payload.get("controller_public_key"), field="controller_public_key"
        )
        secret_hash = self._open_secret_hash(session_id, _timestamp(self._clock()))
        self._spend_guess(session_id, transport, source)
        salt = self._entropy(pake.SALT_BYTES)
        b = int.from_bytes(self._entropy(pake.EPHEMERAL_BYTES), "big")
        handshake_id = _base64url(self._entropy(16))
        exchange = pake.ServerExchange.begin(
            identity=session_id, password=secret_hash, salt=salt, b=b
        )
        host = self._host_context()
        context = {
            "commissioning_id": session_id,
            "controller_public_key_sha256": _controller_key_sha256(key_der),
            "handshake_id": handshake_id,
            "host_public_key": host["host_public_key"],
            "reset_epoch": self._store.get_state().reset_epoch,
            "suite": pake.SUITE,
            "tls_spki_fingerprint": host["tls_spki_fingerprint"],
            "transport": transport,
        }
        self._remember(
            handshake_id,
            _Handshake(
                session_id=session_id,
                secret_hash=secret_hash,
                transport=transport,
                exchange=exchange,
                context=pake.context_bytes(context),
                controller_key_sha256=context["controller_public_key_sha256"],
                expires_at=self._monotonic() + PAKE_HANDSHAKE_SECONDS,
            ),
        )
        return {
            "suite": pake.SUITE,
            "handshake_id": handshake_id,
            "salt": _base64url(salt),
            "B": _base64url(exchange.B_encoded),
            "context": context,
        }

    def pake_confirm(
        self, payload: dict[str, Any], *, transport: str
    ) -> tuple[CommissioningAuthorization, str]:
        """Check the phone's proof; on success, the authorization and the
        Host's own proof. A handshake is answered once, right or wrong."""

        handshake_id = payload.get("handshake_id")
        handshake = (
            self._handshakes.pop(handshake_id, None)
            if isinstance(handshake_id, str)
            else None
        )
        if (
            handshake is None
            or handshake.expires_at <= self._monotonic()
            or handshake.transport != transport
        ):
            raise CommissioningRequestRejected(
                "pake_handshake_unknown", "Start the Setup code exchange again"
            )
        client_public = _decode_base64url(payload.get("A"), field="A")
        client_proof = _decode_base64url(payload.get("client_proof"), field="client_proof")
        try:
            server_proof = handshake.exchange.finish(
                A_encoded=client_public,
                context=handshake.context,
                presented_proof=client_proof,
            )
        except pake.PakeRejected as exc:
            self._store.record_failed_commissioning_attempt(
                session_id=handshake.session_id
            )
            if self._budget is not None:
                self._budget.failed(window=handshake.session_id, transport=transport)
            raise CommissioningRequestRejected(
                "commissioning_denied", "Commissioning session is unavailable"
            ) from exc
        # The window may have been consumed or superseded while the phone was
        # computing; the proof is only as good as the window it was made for.
        try:
            self._store.authorize_commissioning_session(
                session_id=handshake.session_id,
                secret_hash=handshake.secret_hash,
                now=_timestamp(self._clock()),
            )
        except BootstrapStateConflict as exc:
            raise CommissioningRequestRejected(
                "commissioning_denied", "Commissioning session is unavailable"
            ) from exc
        return (
            CommissioningAuthorization(
                handshake.session_id,
                handshake.secret_hash,
                controller_key_sha256=handshake.controller_key_sha256,
            ),
            _base64url(server_proof),
        )

    def pake_claim(
        self, payload: dict[str, Any], *, transport: str
    ) -> dict[str, Any]:
        """Confirm and claim in one step, for a transport with no session to
        hold an authorization between requests (the LAN)."""

        controller = payload.get("controller")
        if not isinstance(controller, dict):
            raise CommissioningRequestRejected(
                "invalid_request", "controller must be an object"
            )
        authorization, server_proof = self.pake_confirm(payload, transport=transport)
        return {**self.claim_controller(authorization, controller), "server_proof": server_proof}

    def _open_secret_hash(self, session_id: str, now: str) -> str:
        try:
            return self._store.commissioning_secret_hash(session_id=session_id, now=now)
        except BootstrapStateConflict as exc:
            raise CommissioningRequestRejected(
                "commissioning_denied", "Commissioning session is unavailable"
            ) from exc

    def _spend_guess(self, session_id: str, transport: str, source: str | None) -> None:
        if self._budget is None:
            return
        try:
            self._budget.take(window=session_id, transport=transport, source=source)
        except GuessRefused as exc:
            raise CommissioningRequestRejected(
                "rate_limited",
                "Too many Setup code attempts; wait before trying again",
                retry_after_ms=exc.retry_after_ms,
            ) from exc

    def _remember(self, handshake_id: str, handshake: _Handshake) -> None:
        now = self._monotonic()
        for key in [k for k, v in self._handshakes.items() if v.expires_at <= now]:
            del self._handshakes[key]
        while len(self._handshakes) >= PAKE_MAX_HANDSHAKES:
            del self._handshakes[next(iter(self._handshakes))]
        self._handshakes[handshake_id] = handshake

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
        if not isinstance(authorization, ControllerAuthorization):
            raise CommissioningRequestRejected(
                "controller_denied", "Host Admin authorization is required"
            )
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
            code = "operation_conflict" if isinstance(exc, BootstrapStateConflict) else "network_stage_failed"
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
                    error_code=code,
                )
            raise CommissioningRequestRejected(
                code, str(exc)
            ) from exc
        return {"operation": operation.to_dict()}

    async def confirm_network(
        self,
        authorization: CommissioningAccess,
        operation_id: str,
    ) -> dict[str, Any]:
        self._require_authorized(authorization)
        if not isinstance(authorization, ControllerAuthorization):
            raise CommissioningRequestRejected(
                "controller_denied", "Host Admin authorization is required"
            )
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
        if not isinstance(authorization, ControllerAuthorization):
            raise CommissioningRequestRejected(
                "controller_denied", "Host Admin authorization is required"
            )
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
        bound = authorization.controller_key_sha256
        if bound is not None and not hmac.compare_digest(
            _controller_key_sha256(_decode_base64url(grant.public_key, field="public_key")),
            bound,
        ):
            raise CommissioningRequestRejected(
                "controller_denied",
                "The Setup code was proven for a different Controller key",
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
                "commissioning_denied"
                if reason == "commissioning session is unavailable"
                else "operation_conflict"
            )
            raise CommissioningRequestRejected(code, reason) from exc
        if self._on_claimed is not None:
            try:
                self._on_claimed()
            except Exception:  # noqa: BLE001 - the claim is committed either way
                logger.exception("claim committed but the claim window did not reopen")
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
