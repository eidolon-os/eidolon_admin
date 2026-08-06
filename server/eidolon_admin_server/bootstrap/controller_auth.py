"""Controller signature verification shared by BLE and LAN authentication."""

from __future__ import annotations

import base64
import json

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import load_der_public_key

from .domain import ControllerGrant


BLE_CONTROLLER_AUTH_PURPOSE = "eidolon-controller-ble-auth-v1"
LOCAL_API_CONTROLLER_AUTH_PURPOSE = "eidolon-controller-local-auth-v1"


class ControllerSignatureError(ValueError):
    """The supplied signature cannot prove the expected Controller key."""


def verify_controller_signature(
    grant: ControllerGrant,
    *,
    challenge: str,
    purpose: str,
    reset_epoch: int,
    signature_value: str,
) -> None:
    unsigned = {
        "challenge": challenge,
        "contract_version": "1",
        "controller_id": grant.controller_id,
        "purpose": purpose,
        "reset_epoch": reset_epoch,
    }
    try:
        signature = _decode_base64url(signature_value)
        public_der = _decode_base64url(grant.public_key)
        public_key = load_der_public_key(public_der)
        if not isinstance(public_key, ec.EllipticCurvePublicKey) or not isinstance(
            public_key.curve, ec.SECP256R1
        ):
            raise ControllerSignatureError("Controller key must be P-256")
        canonical = json.dumps(
            unsigned, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        public_key.verify(signature, canonical, ec.ECDSA(hashes.SHA256()))
    except (InvalidSignature, TypeError, ValueError) as exc:
        if isinstance(exc, ControllerSignatureError):
            raise
        raise ControllerSignatureError("Controller signature is invalid") from exc


def _decode_base64url(value: str) -> bytes:
    if not isinstance(value, str) or not value:
        raise ControllerSignatureError("Controller signature is required")
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, TypeError) as exc:
        raise ControllerSignatureError("Controller value is not base64url") from exc
    canonical = base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii")
    if canonical != value:
        raise ControllerSignatureError("Controller value is not canonical base64url")
    return decoded
