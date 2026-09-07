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

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

# The signed bytes are eidolon_sdk's, not this module's. Hub verifies what this
# issues without either side ever comparing an intermediate value, so a second
# spelling of the derivation, the claim set or the compact framing would be
# discovered only as a device refused at its first commissioning — never as a
# disagreement about any of the three. What stays here is Host policy: how the
# window is chosen, and how a base identity and a one-shot `jti` are minted.
# `derive_voucher_signing_key` is re-exported because the tests that sign for
# this issuer reach it through this module.
from eidolon_sdk.device_foundation.v1 import (  # noqa: F401
    commissioning_voucher_claims,
    derive_voucher_signing_key,
    sign_commissioning_voucher,
)

#: Long enough for a device to leave the setup network, join the Owner's Wi-Fi
#: and reach the Host. The window is not what makes the voucher one-shot — Hub
#: keeps a durable jti ledger for that — it only bounds how long a voucher
#: issued just before a Controller was revoked can still buy a pending Proposal.
DEFAULT_TTL = timedelta(hours=24)


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
        claims = commissioning_voucher_claims(
            device_base_id=base_id,
            owner_domain_id=owner_domain_id,
            operational_spki_sha256=operational_spki_sha256,
            jti=jti,
            expires_at_unix=int(expires_at.timestamp()),
            provenance=provenance,
        )
        return CommissioningVoucher(
            voucher=sign_commissioning_voucher(
                claims=claims,
                signing_key=derive_voucher_signing_key(self.secret),
            ),
            jti=jti,
            device_base_id=base_id,
            expires_at=expires_at,
        )
