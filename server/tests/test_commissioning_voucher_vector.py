"""This issuer against the published commissioning voucher.

The signing key is never sent. This Host derives it from the Owner Domain's
management secret to sign, and Hub derives it again to verify, so neither side
ever sees the other's intermediate value. A disagreement about that derivation
is therefore never reported as one: it arrives as a device refused at first
commissioning, holding a voucher whose signature is perfectly valid under a key
Hub did not compute, with nothing anywhere naming what the two spelled
differently.

`derive_voucher_signing_key` now lives in eidolon_sdk and both sides call it,
so there is one definition rather than two that agree. This test closes the
loop from the issuing end against `golden/commissioning-voucher.json`, which
this repository did not write. Hub's suite verifies that same token with its
production verifier, so the two halves are held to one artifact neither owns.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import eidolon_sdk
import rfc8785

from eidolon_admin_server.app.control_plane.commissioning_vouchers import (
    CommissioningVoucherIssuer,
    derive_voucher_signing_key,
)

GOLDEN = (
    Path(eidolon_sdk.__file__).resolve().parents[1]
    / "contracts/device_foundation/v1/golden/commissioning-voucher.json"
)


def _vector() -> dict:
    assert GOLDEN.exists(), f"the canonical vector is not at {GOLDEN}"
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def test_derivation_matches_the_published_signing_key() -> None:
    """The bytes, not a description of them.

    A change to the `info` string, the length or the hash fails here as a
    vector mismatch naming the derivation. The alternative symptom is a refusal
    on every first commissioning in the fleet, reported as a bad signature.
    """

    voucher = _vector()["voucher"]
    derived = derive_voucher_signing_key(
        bytes.fromhex(voucher["host_management_secret_hex"])
    )
    assert derived.hex() == voucher["signing_key_hex"]


def test_the_published_voucher_is_signed_by_this_issuers_key() -> None:
    """The key this Host would sign with is the key that signed the vector.

    Checked against the published token's own signing input, so nothing here
    restates how a voucher is framed — a drift in the derivation shows up as
    this signature failing to reproduce.
    """

    voucher = _vector()["voucher"]
    signing_key = derive_voucher_signing_key(
        bytes.fromhex(voucher["host_management_secret_hex"])
    )
    header_segment, claims_segment, signature_segment = voucher["compact"].split(".")
    expected = hmac.new(
        signing_key,
        f"{header_segment}.{claims_segment}".encode(),
        hashlib.sha256,
    ).digest()
    assert hmac.compare_digest(expected, _b64url_decode(signature_segment))


def test_issued_voucher_carries_the_published_claim_set() -> None:
    """What this issuer actually mints, against what the contract publishes.

    `jti` is the one claim that cannot match: it is minted fresh for every
    voucher, which is what makes the token one-shot, so it is compared for
    presence rather than for value. Everything else — the member set, each
    value, and the RFC 8785 form of the segment — is held to the vector, so a
    claim added or renamed here fails as a member-set mismatch rather than as
    an unverifiable proof at a device.
    """

    vector = _vector()
    published = vector["voucher"]
    ttl = timedelta(hours=24)
    issuer = CommissioningVoucherIssuer(
        secret=bytes.fromhex(published["host_management_secret_hex"]), ttl=ttl
    )

    issued = issuer.issue(
        owner_domain_id=vector["owner_domain_id"],
        operational_spki_sha256=vector["operational_spki_sha256"],
        device_base_id=vector["device_base_id"],
        provenance=vector["base_identity_provenance"],
        now=datetime.fromtimestamp(published["expires_at_unix"], UTC) - ttl,
    )

    header_segment, claims_segment, _ = issued.voucher.split(".")
    assert json.loads(_b64url_decode(header_segment)) == published["header"]

    claims = json.loads(_b64url_decode(claims_segment))
    assert set(claims) == set(published["claims"])
    for name, value in published["claims"].items():
        if name == "jti":
            continue
        assert claims[name] == value, name
    assert claims["jti"] == issued.jti

    # The segment is the canonical form of the claims it decodes to, which is
    # the property a JSON Schema cannot express and a member reordering breaks.
    assert _b64url_decode(claims_segment) == rfc8785.dumps(claims)
    assert published["claims_canonical_utf8"] == rfc8785.dumps(
        published["claims"]
    ).decode()
