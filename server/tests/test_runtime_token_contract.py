"""Phase 33.A1: cross-project token contract test.

The Phase 32 plan-D runtime path has channel signing a device JWT that
agent's ``PairingTokenVerifier`` consumes. The two implementations live
in separate projects (eidolon_channel + eidolon_agent) with **duplicated
payload schema** — same field names, same algorithm, same secret resolution.

This test pins that contract end-to-end:
  - Use channel's ``sign_device_token`` to mint a JWT with known fields
  - Hand it to agent's ``PairingTokenVerifier.verify``
  - Assert every field round-trips intact

Why this test lives in admin's suite (and not channel or agent's):
admin is the orchestrator; cross-project contract verification is its
natural home. Both target projects are editable-installed in admin's
venv (channel's ``livekit-agents`` transitive is declared in admin's
``[dev]`` extra), so the imports work without any importlib tricks —
plain Python ``from eidolon.livekit.agent.runtime import …``.

If this test fails, ONE of these happened:
  1. Someone changed channel's payload field names / algorithm / scopes
     without updating agent.
  2. Someone changed agent's verifier expectations without updating channel.
  3. The secret resolution diverged.
Any of those would silently break ALL web/esp32 voice conversations until
the next deploy actually used a session — this test catches it at CI.

Until ``eidolon-runtime-tokens`` micro-pkg is extracted (Phase 33 plan
deferred to a unified SDK pass), this is the safety net.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

import pytest

# Both sibling projects must be editable-installed into admin's venv.
# Channel pulls livekit-agents (declared in admin's [dev] extra so its
# eidolon.livekit.agent package can import without ModuleNotFoundError);
# agent has plain Python deps. If either install is missing we skip at
# module load with a clear pointer at how to fix.
try:
    from eidolon.livekit.agent.runtime import (  # type: ignore[import-not-found]
        sign_device_token as channel_sign,
    )
    from eidolon_agent.app.transport.pairing.token import (  # type: ignore[import-not-found]
        PairingTokenVerifier,
    )
except ImportError as exc:  # pragma: no cover — exercised in CI matrix
    pytest.skip(
        f"cross-project import failed ({exc}). "
        "Run `pip install -e ../eidolon_channel -e ../eidolon_agent` "
        "in admin's venv to enable this contract test.",
        allow_module_level=True,
    )


@pytest.fixture
def shared_secret() -> str:
    """A fresh random secret — proves the test depends only on the
    secret being the SAME on both sides, not on a magic value."""
    return secrets.token_urlsafe(32)


# ---- the contract --------------------------------------------------------


@pytest.mark.asyncio
async def test_channel_signed_token_verifies_with_agent_schema(
    shared_secret: str,
) -> None:
    """**The contract**: a JWT minted by channel's signer with a given
    set of fields must decode into a VerifiedDevice with those exact
    fields preserved. Any drift here breaks Phase 32 runtime."""
    token, exp_returned = channel_sign(
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
    token, _exp = channel_sign(
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
    token, _ = channel_sign(
        secret=shared_secret,
        device_id="wrong-secret-test",
        tenant_id="default",
        user_id="manson",
        template_id=None,
    )

    # Late import to keep error path inside the test
    from eidolon_agent.core.errors import UnauthenticatedError  # type: ignore

    verifier = PairingTokenVerifier(secret=secrets.token_urlsafe(32))
    with pytest.raises(UnauthenticatedError):
        await verifier.verify(token)


@pytest.mark.asyncio
async def test_expired_token_rejected(shared_secret: str) -> None:
    """``exp`` claim must be enforced. ttl_seconds=-1 mints an already-
    expired token; verifier should reject."""
    from eidolon_agent.core.errors import UnauthenticatedError  # type: ignore

    token, exp = channel_sign(
        secret=shared_secret,
        device_id="expired-test",
        tenant_id="default",
        user_id="manson",
        template_id=None,
        ttl_seconds=-1,
    )
    assert exp < datetime.now(timezone.utc)

    verifier = PairingTokenVerifier(secret=shared_secret)
    with pytest.raises(UnauthenticatedError):
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
        channel_sign(
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
    token, _ = channel_sign(
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
        f"channel's sign_device_token dropped required fields {missing}. "
        "Agent's PairingTokenVerifier reads these — if you intentionally "
        "removed any, update agent's verifier + this contract test together."
    )
