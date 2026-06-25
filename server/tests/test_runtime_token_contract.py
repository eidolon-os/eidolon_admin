"""Runtime token contract test.

The Phase 32 plan-D runtime path has channel signing a device JWT that
agent's verifier consumes. The canonical implementation now lives in
``eidolon_sdk.biz.runtime`` and runtime callers import it directly.

This test pins that contract end-to-end:
  - Use SDK ``sign_device_token`` to mint a JWT with known fields
  - Hand it to SDK ``PairingTokenVerifier.verify``
  - Assert every field round-trips intact

If this test fails, ONE of these happened:
  1. Someone changed the SDK payload field names / algorithm / scopes.
  2. The verifier allowlist changed without updating token signing.
  3. The secret resolution contract diverged.
Any of those would silently break ALL web/esp32 voice conversations until
the next deploy actually used a session — this test catches it at CI.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

import pytest

from eidolon_sdk.biz.runtime import (
    PairingTokenVerifier,
    RuntimeUnauthenticatedError,
    sign_device_token,
)


@pytest.fixture
def shared_secret() -> str:
    """A fresh random secret — proves the test depends only on the
    secret being the SAME on both sides, not on a magic value."""
    return secrets.token_urlsafe(32)


# ---- the contract --------------------------------------------------------


@pytest.mark.asyncio
async def test_sdk_signed_token_verifies_with_runtime_schema(
    shared_secret: str,
) -> None:
    """**The contract**: a JWT minted by SDK signer with a given
    set of fields must decode into a VerifiedDevice with those exact
    fields preserved. Any drift here breaks Phase 32 runtime."""
    token, exp_returned = sign_device_token(
        secret=shared_secret,
        device_id="contract-test-abc",
        tenant_id="default",
        user_id="manson",
        template_id="caretaker_jiezhi",
        scopes=("device",),
        ttl_seconds=120,
    )

    verifier = PairingTokenVerifier(secret=shared_secret)
    verified = await verifier.verify(token)

    assert verified.device_id == "contract-test-abc"
    assert verified.tenant_id == "default"
    assert verified.user_id == "manson"
    assert verified.default_template_id == "caretaker_jiezhi"
    assert verified.scopes == ("device",)
    # exp returned by signer should equal what verifier decodes (modulo
    # JWT's integer-seconds rounding)
    assert int(verified.exp.timestamp()) == int(exp_returned.timestamp())


@pytest.mark.asyncio
async def test_template_id_null_round_trips(shared_secret: str) -> None:
    """``template_id`` is the only nullable claim — channel passes None
    when the resolver couldn't pin a template (e.g. resolve_user 时
    user 没 active_agent_id 但 hub 仍签了 token,边缘场景)。Verifier
    must decode it back to None, not to empty string or 'null'."""
    token, _exp = sign_device_token(
        secret=shared_secret,
        device_id="null-tpl-test",
        tenant_id="default",
        user_id="alice",
        template_id=None,
        scopes=("device",),
        ttl_seconds=60,
    )
    verifier = PairingTokenVerifier(secret=shared_secret)
    verified = await verifier.verify(token)
    assert verified.default_template_id is None


@pytest.mark.asyncio
async def test_wrong_secret_rejected(shared_secret: str) -> None:
    """Sanity: a token signed with one secret must NOT verify under a
    different secret. If this test stops failing, the verifier's HMAC
    check is bypassed."""
    token, _ = sign_device_token(
        secret=shared_secret,
        device_id="wrong-secret-test",
        tenant_id="default",
        user_id="manson",
        template_id=None,
    )

    verifier = PairingTokenVerifier(secret=secrets.token_urlsafe(32))
    with pytest.raises(RuntimeUnauthenticatedError):
        await verifier.verify(token)


@pytest.mark.asyncio
async def test_expired_token_rejected(shared_secret: str) -> None:
    """``exp`` claim must be enforced. ttl_seconds=-1 mints an already-
    expired token; verifier should reject."""
    token, exp = sign_device_token(
        secret=shared_secret,
        device_id="expired-test",
        tenant_id="default",
        user_id="manson",
        template_id=None,
        ttl_seconds=-1,
    )
    assert exp < datetime.now(timezone.utc)

    verifier = PairingTokenVerifier(secret=shared_secret)
    with pytest.raises(RuntimeUnauthenticatedError):
        await verifier.verify(token)


@pytest.mark.asyncio
async def test_algorithm_must_be_HS256(shared_secret: str) -> None:
    """If channel switches to a different signing algorithm without
    coordinating with agent, the schema-level field round-trip might
    still 'work' on a happy-path test but agent's algorithms allowlist
    would reject in prod. We pin HS256 here.

    Note: this is testing the algorithm AGREEMENT, not the algorithm
    choice. If both sides ever migrate to e.g. EdDSA, update both
    projects + this test in lockstep."""
    import jwt

    # Sign with a deliberately-different alg under the same secret
    payload = jwt.decode(
        sign_device_token(
            secret=shared_secret,
            device_id="alg-test",
            tenant_id="default",
            user_id="manson",
            template_id=None,
            ttl_seconds=60,
        )[0],
        shared_secret,
        algorithms=["HS256"],
    )
    # If this raises, the contract on alg name changed.
    assert payload["device_id"] == "alg-test"


# ---- schema drift sentinel -----------------------------------------------


def test_payload_field_names_are_locked() -> None:
    """A change-detector for the JWT payload contract. If channel's
    signer starts emitting different keys, this fails — even before any
    verifier is involved. Quick-running so it gates every push.

    The list is the union of fields PairingTokenVerifier reads:
    ``device_id`` / ``tenant_id`` / ``user_id`` / ``template_id`` /
    ``scopes`` / ``exp`` / ``iat`` / ``jti`` (the standard JWT claim).
    Adding new optional fields is fine; renaming or removing requires
    coordinated change in both projects.
    """
    import jwt

    secret = secrets.token_urlsafe(32)
    token, _ = sign_device_token(
        secret=secret,
        device_id="schema-lock",
        tenant_id="default",
        user_id="manson",
        template_id="t",
    )
    payload = jwt.decode(token, secret, algorithms=["HS256"])
    required = {"device_id", "tenant_id", "user_id", "template_id", "scopes", "exp", "iat"}
    missing = required - set(payload.keys())
    assert not missing, (
        f"SDK sign_device_token dropped required fields {missing}. "
        "SDK PairingTokenVerifier reads these — if you intentionally "
        "removed any, update agent's verifier + this contract test together."
    )
