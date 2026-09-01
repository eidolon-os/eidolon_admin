"""Minting the standing a Body needs before it may ask to be admitted.

A device carries no factory identity material. What lets it ask is a one-shot
voucher this Host signs during a commissioning the Owner is physically present
for, through a Controller this Owner has already accepted. The voucher names a
base identity and binds it to the operational key the device generated for
itself, so it is worth nothing to anything holding a different key.

Two rules shape the code below and are worth stating before the code says them
less clearly:

* the base identity is minted here, never accepted from the device — a value a
  device chose for itself would become permanent history the moment it was
  signed; and
* an existing base identity is re-signed only when Hub confirms it issued that
  identity to exactly this key, which is what lets a removed Body come back as
  itself rather than as a stranger.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import rfc8785
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

#: Domain separation from the management credential the same secret signs.
#: Without it a management JWT could be replayed as a commissioning proof.
VOUCHER_KEY_INFO = b"eidolon-commissioning-voucher-v1"
VOUCHER_PURPOSE = "eidolon-commissioning-voucher-v1"

#: Long enough for a device to leave the setup network, join the Owner's Wi-Fi
#: and reach the Host. The window is not what makes the voucher one-shot — Hub
#: keeps a durable jti ledger for that — it only bounds how long a voucher
#: issued just before a Controller was revoked can still buy a pending Proposal.
DEFAULT_TTL = timedelta(hours=24)


def derive_voucher_signing_key(management_secret: bytes) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(), length=32, salt=None, info=VOUCHER_KEY_INFO
    ).derive(management_secret)


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


@dataclass(frozen=True, slots=True)
class CommissioningVoucher:
    voucher: str
    jti: str
    device_base_id: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class CommissioningVoucherIssuer:
    """Sign one voucher for one operational key."""

    secret: bytes
    ttl: timedelta = DEFAULT_TTL

    def issue(
        self,
        *,
        owner_domain_id: str,
        operational_spki_sha256: str,
        device_base_id: str | None = None,
        provenance: str = "minted",
        now: datetime | None = None,
    ) -> CommissioningVoucher:
        issued_at = now or datetime.now(UTC)
        expires_at = issued_at + self.ttl
        base_id = device_base_id or "device-base-" + secrets.token_bytes(32).hex()
        jti = "jti-" + secrets.token_hex(16)
        claims = {
            "base_identity_provenance": provenance,
            "device_base_id": base_id,
            "exp": int(expires_at.timestamp()),
            "jti": jti,
            "operational_spki_sha256": operational_spki_sha256,
            "owner_domain_id": owner_domain_id,
            "purpose": VOUCHER_PURPOSE,
        }
        signing_input = (
            f"{_b64(rfc8785.dumps({'alg': 'HS256', 'typ': 'JWT'}))}."
            f"{_b64(rfc8785.dumps(claims))}"
        )
        signature = hmac.new(
            derive_voucher_signing_key(self.secret), signing_input.encode(), hashlib.sha256
        ).digest()
        return CommissioningVoucher(
            voucher=f"{signing_input}.{_b64(signature)}",
            jti=jti,
            device_base_id=base_id,
            expires_at=expires_at,
        )
