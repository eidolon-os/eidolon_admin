"""The phone's half of the Setup code PAKE, for Host tests (ADR-0008).

The code is never sent to a Host any more, so a test that needs a claim proves
the code the way a phone does: pake.start, the client's arithmetic, pake.confirm,
and a check of the Host's own proof. Written against ``pake``'s primitives only,
the same ones the golden vector pins.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from typing import Any

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from eidolon_admin_server.bootstrap import pake
from eidolon_admin_server.bootstrap.commissioning_protocol import (
    CommissioningProtocolSession,
)
from eidolon_admin_server.bootstrap.commissioning_service import (
    CommissioningAuthorization,
    CommissioningService,
)


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


#: Keys for a CommissioningService built without a BootstrapService around it.
FIXED_HOST_CONTEXT = {
    "host_public_key": b64(hashlib.sha256(b"test host key").digest()),
    "tls_spki_fingerprint": "sha256:" + b64(hashlib.sha256(b"test tls key").digest()),
}


def fixed_host_context() -> dict[str, str]:
    return dict(FIXED_HOST_CONTEXT)


def controller_claim(display_name: str = "Bench tablet") -> dict[str, str]:
    der = (
        ec.generate_private_key(ec.SECP256R1())
        .public_key()
        .public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    )
    return {
        "controller_id": f"ectrl-{hashlib.sha256(der).hexdigest()[:20]}",
        "public_key": b64(der),
        "display_name": display_name,
        "platform": "android",
    }


def start_payload(commissioning_id: str, controller: dict[str, str]) -> dict[str, str]:
    return {
        "suite": pake.SUITE,
        "commissioning_id": commissioning_id,
        "controller_public_key": controller["public_key"],
    }


def phone_proof(
    start: dict[str, Any], *, code: str, overrides: dict[str, Any] | None = None
) -> tuple[dict[str, str], str]:
    """The confirm payload, and the server proof an honest Host must answer.
    ``overrides`` changes what the phone believes it pinned."""

    group = pake.GROUP
    context = {**start["context"], **(overrides or {})}
    salt = unb64(start["salt"])
    B = group.public_value(unb64(start["B"]))
    a = int.from_bytes(secrets.token_bytes(32), "big")
    A = group.client_public(a)
    u = group.scrambler(A, B)
    x = group.private_key(context["commissioning_id"], pake.password_for_code(code), salt)
    K = pake.session_key(group.client_secret(B, x, a, u))
    transcript = pake.transcript_hash(pake.context_bytes(context), salt, A, B)
    keys = pake.confirmation_keys(K, transcript)
    proof = pake.client_proof(keys, transcript)
    return (
        {
            "handshake_id": start["handshake_id"],
            "A": b64(group.pad(A)),
            "client_proof": b64(proof),
        },
        b64(pake.server_proof(keys, transcript, proof)),
    )


def prove_setup_code(
    commissioning: CommissioningService,
    *,
    session_id: str,
    code: str,
    controller: dict[str, str] | None = None,
    transport: str = "ble",
) -> CommissioningAuthorization:
    """What ``authorize(session_id, secret)`` used to give a test, the way a
    phone gets it now. The authorization claims only with ``controller``'s
    key; a wrong code raises CommissioningRequestRejected as the Host does."""

    controller = controller or controller_claim()
    start = commissioning.pake_start(
        start_payload(session_id, controller), transport=transport
    )
    confirm, expected = phone_proof(start, code=code)
    authorization, server_proof = commissioning.pake_confirm(confirm, transport=transport)
    assert server_proof == expected, "the Host did not prove the code back"
    return authorization


async def prove_over_ble(
    session: CommissioningProtocolSession,
    *,
    commissioning_id: str,
    code: str,
    controller: dict[str, str],
) -> dict[str, Any]:
    """pake.start then pake.confirm on one link. Returns the confirm answer
    envelope, refusal or not; a refused start is returned as it is."""

    started = await session.handle(
        _request("pake.start", start_payload(commissioning_id, controller))
    )
    if not started["ok"]:
        return started
    confirm, expected = phone_proof(started["result"], code=code)
    answer = await session.handle(_request("pake.confirm", confirm))
    if answer["ok"]:
        assert answer["result"]["server_proof"] == expected
    return answer


def claim_over_lan(
    bootstrap: Any,
    *,
    commissioning_id: str,
    code: str,
    controller: dict[str, str],
    source: str | None = None,
) -> dict[str, Any]:
    """A LAN claim through BootstrapService, as the Local API drives it."""

    start = bootstrap.lan_pake_start(
        start_payload(commissioning_id, controller), source=source
    )
    confirm, expected = phone_proof(start, code=code)
    claimed = bootstrap.lan_pake_claim({**confirm, "controller": controller})
    assert claimed["server_proof"] == expected
    return claimed


def _request(operation: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "contract_version": "1",
        "request_id": "11111111-1111-4111-8111-111111111111",
        "operation": operation,
        "payload": payload,
    }
