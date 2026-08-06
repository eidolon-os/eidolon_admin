"""JSON request protocol carried only after the pinned TLS handshake."""

from __future__ import annotations

import base64
import json
import logging
import secrets
import time
import uuid
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import load_der_public_key

from .commissioning_service import (
    CommissioningAccess,
    CommissioningAuthorization,
    CommissioningRequestRejected,
    CommissioningService,
)


logger = logging.getLogger("eidolon.bootstrap.commissioning")
_RETRYABLE_CODES = {
    "bootstrap_unavailable",
    "internal_error",
    "network_confirm_failed",
    "network_rollback_failed",
    "network_stage_failed",
}


class CommissioningProtocolSession:
    """Per-link authentication and request dispatch; never shared across centrals."""

    def __init__(self, service: CommissioningService) -> None:
        self._service = service
        self._authorization: CommissioningAccess | None = None
        self._controller_challenge: tuple[str, str, int, float] | None = None

    async def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        request_id = request.get("request_id")
        try:
            self._validate_request_id(request_id)
            if request.get("contract_version") != "1":
                raise CommissioningRequestRejected(
                    "unsupported_contract", "Unsupported commissioning contract"
                )
            operation = request.get("operation")
            payload = request.get("payload", {})
            if not isinstance(payload, dict):
                raise CommissioningRequestRejected(
                    "invalid_request", "payload must be an object"
                )
            if operation == "session.authenticate":
                if self._authorization is not None:
                    raise CommissioningRequestRejected(
                        "operation_conflict", "Session is already authenticated"
                    )
                self._authorization = self._service.authorize(
                    session_id=payload.get("commissioning_id"),
                    secret=payload.get("setup_code"),
                )
                result = self._service.status(self._authorization)
            elif operation == "controller.challenge":
                if self._authorization is not None:
                    raise CommissioningRequestRejected(
                        "operation_conflict", "Session is already authenticated"
                    )
                controller_id = payload.get("controller_id")
                authorization = self._service.authorize_controller(controller_id)
                challenge = (
                    base64.urlsafe_b64encode(secrets.token_bytes(32))
                    .rstrip(b"=")
                    .decode("ascii")
                )
                reset_epoch = authorization.grant.reset_epoch
                self._controller_challenge = (
                    controller_id,
                    challenge,
                    reset_epoch,
                    time.monotonic() + 60,
                )
                result = {
                    "contract_version": "1",
                    "purpose": "eidolon-controller-ble-auth-v1",
                    "controller_id": controller_id,
                    "challenge": challenge,
                    "reset_epoch": reset_epoch,
                }
            elif operation == "controller.authenticate":
                if self._authorization is not None:
                    raise CommissioningRequestRejected(
                        "operation_conflict", "Session is already authenticated"
                    )
                self._authorization = self._authenticate_controller(payload)
                result = self._service.status(self._authorization)
            else:
                authorization = self._authorization
                if authorization is None:
                    raise CommissioningRequestRejected(
                        "commissioning_denied", "Authenticate this session first"
                    )
                if operation == "setup.status":
                    result = self._service.status(authorization)
                elif operation == "wifi.scan":
                    result = await self._service.scan_networks(authorization)
                elif operation == "wifi.configure":
                    result = await self._service.configure_network(
                        authorization, payload
                    )
                elif operation == "wifi.confirm":
                    result = await self._service.confirm_network(
                        authorization, payload.get("operation_id")
                    )
                elif operation == "wifi.rollback":
                    result = await self._service.rollback_network(
                        authorization, payload.get("operation_id")
                    )
                elif operation == "claim.complete":
                    if not isinstance(authorization, CommissioningAuthorization):
                        raise CommissioningRequestRejected(
                            "controller_denied",
                            "Initial credential is required to claim",
                        )
                    result = self._service.claim_controller(authorization, payload)
                else:
                    raise CommissioningRequestRejected(
                        "unknown_operation", "Unknown commissioning operation"
                    )
            return {
                "contract_version": "1",
                "request_id": request_id,
                "ok": True,
                "result": result,
            }
        except CommissioningRequestRejected as exc:
            return {
                "contract_version": "1",
                "request_id": request_id if isinstance(request_id, str) else None,
                "ok": False,
                "error": {
                    "code": exc.code,
                    "message": str(exc),
                    "retryable": exc.code in _RETRYABLE_CODES,
                },
            }
        except Exception:
            logger.exception("unexpected commissioning request failure")
            return {
                "contract_version": "1",
                "request_id": request_id if isinstance(request_id, str) else None,
                "ok": False,
                "error": {
                    "code": "internal_error",
                    "message": "The Host could not complete this Setup request",
                    "retryable": True,
                },
            }

    def _authenticate_controller(self, payload: dict[str, Any]) -> CommissioningAccess:
        challenge_state = self._controller_challenge
        self._controller_challenge = None
        if challenge_state is None or challenge_state[3] <= time.monotonic():
            raise CommissioningRequestRejected(
                "controller_denied", "Controller challenge is missing or expired"
            )
        controller_id, challenge, reset_epoch, _ = challenge_state
        if (
            payload.get("controller_id") != controller_id
            or payload.get("challenge") != challenge
            or payload.get("reset_epoch") != reset_epoch
        ):
            raise CommissioningRequestRejected(
                "controller_denied", "Controller challenge does not match"
            )
        authorization = self._service.authorize_controller(controller_id)
        signature_value = payload.get("signature")
        if not isinstance(signature_value, str):
            raise CommissioningRequestRejected(
                "controller_denied", "Controller signature is required"
            )
        try:
            signature = base64.urlsafe_b64decode(
                signature_value + "=" * (-len(signature_value) % 4)
            )
            public_der = base64.urlsafe_b64decode(
                authorization.grant.public_key
                + "=" * (-len(authorization.grant.public_key) % 4)
            )
            public_key = load_der_public_key(public_der)
            if not isinstance(public_key, ec.EllipticCurvePublicKey):
                raise ValueError("controller key is not EC")
            unsigned = {
                "challenge": challenge,
                "contract_version": "1",
                "controller_id": controller_id,
                "purpose": "eidolon-controller-ble-auth-v1",
                "reset_epoch": reset_epoch,
            }
            canonical = json.dumps(
                unsigned, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            public_key.verify(signature, canonical, ec.ECDSA(hashes.SHA256()))
        except (InvalidSignature, ValueError, TypeError) as exc:
            raise CommissioningRequestRejected(
                "controller_denied", "Controller signature is invalid"
            ) from exc
        return authorization

    @staticmethod
    def _validate_request_id(value: Any) -> None:
        if not isinstance(value, str):
            raise CommissioningRequestRejected(
                "invalid_request", "request_id must be a UUID"
            )
        try:
            parsed = uuid.UUID(value)
        except ValueError as exc:
            raise CommissioningRequestRejected(
                "invalid_request", "request_id must be a UUID"
            ) from exc
        if str(parsed) != value:
            raise CommissioningRequestRejected(
                "invalid_request", "request_id must use canonical UUID encoding"
            )
