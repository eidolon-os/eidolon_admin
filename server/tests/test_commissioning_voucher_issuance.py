"""What a Host will and will not sign when a Body asks for standing.

The device carries no factory identity material, so the only thing that makes a
first Proposal possible is this signature. Two rules are worth a test each: the
base identity is minted here rather than accepted from the device, and an
identity is only re-signed when Hub says it issued that identity to this very
key. Without the second rule a removed Body would come back as a stranger;
without the first, a Body could name itself and have the Host make it true.
"""

from __future__ import annotations

import base64
import hashlib
import hmac

import pytest
import rfc8785
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from eidolon_sdk.device_foundation.v1 import (
    BusinessOwnerId,
    ControllerActorRef,
    OwnerDomainId,
)

from eidolon_admin_server.app.control_plane.commissioning_vouchers import (
    CommissioningVoucherIssuer,
    derive_voucher_signing_key,
)
from eidolon_admin_server.app.control_plane.contracts import (
    ControllerCommissioningVoucherRequest,
)
from eidolon_admin_server.app.control_plane.errors import AuthorityFailure
from eidolon_admin_server.app.control_plane.hub_credentials import HubAdminCredentialIssuer
from eidolon_admin_server.app.control_plane.service import ControlPlaneService

SECRET = b"installation-management-secret-32b"
DOMAIN = OwnerDomainId("owner-domain-a")
BUSINESS_OWNER = BusinessOwnerId("owner_account_a")
ACTOR = ControllerActorRef(
    principal_id="ectrl-0123456789abcdef0123",
    owner_domain_id=DOMAIN,
    granted_scopes=("device.read", "device.claim.approve"),
    authentication_strength="software",
)
KNOWN_BASE_ID = "device-base-" + "ab" * 32


def _operational_public_key() -> tuple[str, str]:
    key = ec.derive_private_key(0x234567891, ec.SECP256R1())
    der = key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return (
        "p256-spki:" + base64.urlsafe_b64encode(der).rstrip(b"=").decode(),
        "sha256:" + hashlib.sha256(der).hexdigest(),
    )


class Hub:
    def __init__(self, answer: dict) -> None:
        self.answer = answer
        self.asked: list[str] = []

    async def base_identity_for_key(
        self, *, authorization: str, operational_key_id: str
    ) -> dict:
        self.asked.append(operational_key_id)
        return self.answer


def _service(hub: Hub | None, *, vouchers: CommissioningVoucherIssuer | None = None):
    return ControlPlaneService(
        directory=object(),
        data=object(),
        workspace=object(),
        hub=hub,
        kernel=object(),
        memory=object(),
        activity=object(),
        hub_credentials=HubAdminCredentialIssuer(secret=SECRET, ttl_seconds=60),
        commissioning_vouchers=vouchers
        if vouchers is not None
        else CommissioningVoucherIssuer(secret=SECRET),
    )


def _request() -> ControllerCommissioningVoucherRequest:
    _public_key, key_id = _operational_public_key()
    return ControllerCommissioningVoucherRequest(
        contract_version="1",
        actor=ACTOR,
        business_owner_id=BUSINESS_OWNER,
        owner_domain_id=DOMAIN,
        operational_spki_sha256=key_id,
    )


def _claims(voucher: str) -> dict:
    import json

    payload = voucher.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


@pytest.mark.asyncio
async def test_a_voucher_is_bound_to_the_key_and_verifiable_with_the_derived_secret() -> None:
    public_key, key_id = _operational_public_key()
    hub = Hub({"device_base_id": None, "requires_fresh_presence": False})
    issued = await _service(hub).issue_commissioning_voucher(payload=_request())

    header, claims, signature = issued.voucher.split(".")
    expected = hmac.new(
        derive_voucher_signing_key(SECRET), f"{header}.{claims}".encode(), hashlib.sha256
    ).digest()
    assert base64.urlsafe_b64encode(expected).rstrip(b"=").decode() == signature
    decoded = _claims(issued.voucher)
    assert decoded["operational_spki_sha256"] == key_id
    assert decoded["purpose"] == "eidolon-commissioning-voucher-v1"
    assert decoded["jti"] == issued.jti
    assert decoded["device_base_id"].startswith("device-base-")
    assert public_key not in issued.voucher


@pytest.mark.asyncio
async def test_two_bodies_never_receive_the_same_minted_identity() -> None:
    service = _service(Hub({"device_base_id": None, "requires_fresh_presence": False}))
    first = await service.issue_commissioning_voucher(payload=_request())
    second = await service.issue_commissioning_voucher(payload=_request())
    assert first.device_base_id != second.device_base_id
    assert first.jti != second.jti


@pytest.mark.asyncio
async def test_the_identity_hub_issued_to_this_key_is_the_one_signed() -> None:
    """A Body that was removed comes back on the identity it already had.

    Nothing the device says decides this: the Host asks Hub about the key, and
    Hub answers with what it issued to that key. That is what keeps the Claim
    generation fence attached to the same Body across a removal.
    """

    _public_key, key_id = _operational_public_key()
    hub = Hub({"device_base_id": KNOWN_BASE_ID, "requires_fresh_presence": False})
    issued = await _service(hub).issue_commissioning_voucher(payload=_request())
    assert issued.device_base_id == KNOWN_BASE_ID
    assert hub.asked == [key_id]


@pytest.mark.asyncio
async def test_a_key_hub_has_never_issued_to_gets_a_new_identity() -> None:
    hub = Hub({"device_base_id": None, "requires_fresh_presence": False})
    issued = await _service(hub).issue_commissioning_voucher(payload=_request())
    assert issued.device_base_id.startswith("device-base-")
    assert issued.device_base_id != KNOWN_BASE_ID


@pytest.mark.asyncio
async def test_a_host_without_the_signing_secret_refuses_rather_than_improvises() -> None:
    service = ControlPlaneService(
        directory=object(),
        data=object(),
        workspace=object(),
        hub=None,
        kernel=object(),
        memory=object(),
        activity=object(),
        hub_credentials=HubAdminCredentialIssuer(secret=SECRET, ttl_seconds=60),
    )
    with pytest.raises(AuthorityFailure):
        await service.issue_commissioning_voucher(payload=_request())


def test_the_signing_key_is_not_the_management_secret_itself() -> None:
    """A management token must never be replayable as a commissioning proof."""

    assert derive_voucher_signing_key(SECRET) != SECRET
    assert derive_voucher_signing_key(SECRET) == derive_voucher_signing_key(SECRET)
    assert rfc8785.dumps({"a": 1}) == b'{"a":1}'
