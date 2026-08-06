"""Runtime token contract test.

Channel signs a device JWT and eidolon_agent verifies it. The contract is
the explicit runtime identity tuple: owner, companion, device, memory realm,
and genome.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

import pytest

from eidolon_sdk.biz.runtime import (
    RuntimeTokenVerifier,
    RuntimeUnauthenticatedError,
    sign_runtime_token,
)


@pytest.fixture
def shared_secret() -> str:
    return secrets.token_urlsafe(32)


def _sign_device_token(*, secret: str, device_id: str, **kwargs):
    return sign_runtime_token(
        secret=secret,
        device_id=device_id,
        schema_version=kwargs.pop("schema_version", "eidolon.persona_genome"),
        genome_hash=kwargs.pop("genome_hash", "pg_contract"),
        realizer_version=kwargs.pop("realizer_version", "eidolon.persona_realizer"),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_sdk_signed_token_verifies_with_runtime_schema(shared_secret: str) -> None:
    token, exp_returned = _sign_device_token(
        secret=shared_secret,
        device_id="contract-test-abc",
        owner_id="owner-a",
        companion_id="companion-a",
        memory_realm_id="realm-a",
        genome_id="genome-a",
        scopes=("device",),
        ttl_seconds=120,
    )

    verified = await RuntimeTokenVerifier(secret=shared_secret).verify(token)

    assert verified.device_id == "contract-test-abc"
    assert verified.owner_id == "owner-a"
    assert verified.companion_id == "companion-a"
    assert verified.memory_realm_id == "realm-a"
    assert verified.genome_id == "genome-a"
    assert verified.schema_version == "eidolon.persona_genome"
    assert verified.genome_hash == "pg_contract"
    assert verified.realizer_version == "eidolon.persona_realizer"
    assert verified.scopes == ("device",)
    assert int(verified.exp.timestamp()) == int(exp_returned.timestamp())


@pytest.mark.asyncio
async def test_wrong_secret_rejected(shared_secret: str) -> None:
    token, _ = _sign_device_token(
        secret=shared_secret,
        device_id="wrong-secret-test",
        owner_id="owner-a",
        companion_id="companion-a",
        memory_realm_id="realm-a",
        genome_id="genome-a",
    )

    verifier = RuntimeTokenVerifier(secret=secrets.token_urlsafe(32))
    with pytest.raises(RuntimeUnauthenticatedError):
        await verifier.verify(token)


@pytest.mark.asyncio
async def test_expired_token_rejected(shared_secret: str) -> None:
    token, exp = _sign_device_token(
        secret=shared_secret,
        device_id="expired-test",
        owner_id="owner-a",
        companion_id="companion-a",
        memory_realm_id="realm-a",
        genome_id="genome-a",
        ttl_seconds=-1,
    )
    assert exp < datetime.now(timezone.utc)

    verifier = RuntimeTokenVerifier(secret=shared_secret)
    with pytest.raises(RuntimeUnauthenticatedError):
        await verifier.verify(token)


def test_payload_field_names_are_locked(shared_secret: str) -> None:
    import jwt

    token, _ = _sign_device_token(
        secret=shared_secret,
        device_id="schema-lock",
        owner_id="owner-a",
        companion_id="companion-a",
        memory_realm_id="realm-a",
        genome_id="genome-a",
    )
    payload = jwt.decode(token, shared_secret, algorithms=["HS256"])
    required = {
        "runtime_token_version",
        "device_id",
        "owner_id",
        "companion_id",
        "memory_realm_id",
        "genome_id",
        "schema_version",
        "genome_hash",
        "realizer_version",
        "scopes",
        "exp",
        "iat",
    }
    missing = required - set(payload.keys())
    assert not missing
    assert payload["runtime_token_version"] == 4
    assert "actor_kind" not in payload
    assert "actor_id" not in payload
