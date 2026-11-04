#!/usr/bin/env python3
"""Write the Host claim SRP-6a golden vector, or check that it is current.

Every intermediate value of one handshake, plus the inputs a correct
implementation must refuse, so the phone's Dart code and the Host's Python code
are held to the same bytes rather than to each other's opinion of them. The
arithmetic itself is first checked against RFC 5054 Appendix B — the published
vector, not one of ours — and the file carries that vector too, so the phone
checks it as well.

Fixed inputs only: no randomness here, so the file changes when and only when
the protocol does. A test runs this with ``--check``.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ADMIN_ROOT = HERE.parents[2]
OUTPUT = HERE / "golden" / "host-claim-srp6a-v1.json"
sys.path.insert(0, str(ADMIN_ROOT / "server"))

from eidolon_admin_server.bootstrap import pake  # noqa: E402

RFC5054_1024_N = int(
    "eeaf0ab9adb38dd69c33f80afa8fc5e86072618775ff3c0b9ea2314c9c256576"
    "d674df7496ea81d3383b4813d692c6e0e0d5d8e250b98be48e495c1d6089dad1"
    "5dc7d7b46154d6b6ce8ef4ad69b15d4982559b297bcf1885c529f566660e57ec"
    "68edbc3c05726cc02fd4cbf4976eaa9afd5138fe8376435b9fc61d2fc0eb06e3",
    16,
)
#: RFC 5054 Appendix B, as published.
RFC5054_APPENDIX_B = {
    "identity": "alice",
    "password": "password123",
    "salt": "beb25379d1a8581eb5a727673a2441ee",
    "a": "60975527035cf2ad1989806f0407210bc81edc04e2762a56afd529ddda2d4393",
    "b": "e487cb59d31ac550471e81f00f6928e01dda08e974a004f49e61f5d105284d20",
    "k": "7556aa045aef2cdd07abaf0f665c3e818913186f",
    "x": "94b7555aabe9127cc58ccf4993db6cf84d16c124",
    "v": (
        "7e273de8696ffc4f4e337d05b4b375beb0dde1569e8fa00a9886d8129bada1f1"
        "822223ca1a605b530e379ba4729fdc59f105b4787e5186f5c671085a1447b52a"
        "48cf1970b4fb6f8400bbf4cebfbb168152e08ab5ea53d15c1aff87b2b9da6e04"
        "e058ad51cc72bfc9033b564e26480d78e955a5e29e7ab245db2be315e2099afb"
    ),
    "A": (
        "61d5e490f6f1b79547b0704c436f523dd0e560f0c64115bb72557ec44352e890"
        "3211c04692272d8b2d1a5358a2cf1b6e0bfcf99f921530ec8e39356179eae45e"
        "42ba92aeaced825171e1e8b9af6d9c03e1327f44be087ef06530e69f66615261"
        "eef54073ca11cf5858f0edfdfe15efeab349ef5d76988a3672fac47b0769447b"
    ),
    "B": (
        "bd0c61512c692c0cb6d041fa01bb152d4916a1e77af46ae105393011baf38964"
        "dc46a0670dd125b95a981652236f99d9b681cbf87837ec996c6da04453728610"
        "d0c6ddb58b318885d7d82c7f8deb75ce7bd4fbaa37089e6f9c6059f388838e7a"
        "00030b331eb76840910440b1b27aaeaeeb4012b7d7665238a8e3fb004b117b58"
    ),
    "u": "ce38b9593487da98554ed47d70a7ae5f462ef019",
    "S": (
        "b0dc82babcf30674ae450c0287745e7990a3381f63b387aaf271a10d233861e3"
        "59b48220f7c4693c9ae12b0a6f67809f0876e2d013800d6c41bb59b6d5979b5c"
        "00a172b4a2a5903a0bdcaf8a709585eb2afafa8f3499b200210dcc1f10eb3394"
        "3cd67fc88a2f39a4be5bec4ec0a3212dc346d7e474b29ede8a469ffeca686e5a"
    ),
}

CODE = "40718256"
COMMISSIONING_ID = "3f6b8a62-0c1e-4b8e-9a51-2d7c4e9f1a03"
SALT = bytes.fromhex("9c41d7e2a05b3f68c2e1904d7ab35e16")
A_PRIVATE = int("5f2c8e17a4d39b60e1c7f4a28d0b95e36c71f8a2d4e05b9c3a7e6f1d2b8c4a90", 16)
B_PRIVATE = int("c83e5a91f07d2b46e9a1c3f58d72b0e4a61f9c2d7e35b8a04c6d1e9f2a7b5c38", 16)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _stand_in(label: str, size: int = 32) -> str:
    """A canonical base64url value of the right size that names what it stands for."""

    return _b64(hashlib.sha256(label.encode("ascii")).digest()[:size])


CONTEXT = {
    "commissioning_id": COMMISSIONING_ID,
    "controller_public_key_sha256": _stand_in("golden controller key"),
    "handshake_id": _stand_in("golden handshake", 16),
    "host_public_key": _stand_in("golden host key"),
    "reset_epoch": 2,
    "suite": pake.SUITE,
    "tls_spki_fingerprint": "sha256:" + _stand_in("golden tls key"),
    "transport": "ble",
}


def _hex(value: int, width: int | None = None) -> str:
    if width is None:
        width = max(1, (value.bit_length() + 7) // 8)
    return value.to_bytes(width, "big").hex()


def check_rfc5054() -> dict[str, object]:
    group = pake.SrpGroup(RFC5054_1024_N, 2, "sha1")
    vector = RFC5054_APPENDIX_B
    salt = bytes.fromhex(vector["salt"])
    a, b = int(vector["a"], 16), int(vector["b"], 16)
    x = group.private_key(vector["identity"], vector["password"], salt)
    v = group.verifier(vector["identity"], vector["password"], salt)
    A, B = group.client_public(a), group.server_public(v, b)
    u = group.scrambler(A, B)
    computed = {
        "k": group.k,
        "x": x,
        "v": v,
        "A": A,
        "B": B,
        "u": u,
        "S": group.server_secret(A, v, u, b),
    }
    for name, value in computed.items():
        if value != int(vector[name], 16):
            raise SystemExit(f"RFC 5054 Appendix B: {name} does not match")
    if group.client_secret(B, x, a, u) != computed["S"]:
        raise SystemExit("RFC 5054 Appendix B: the two sides disagree on S")
    return {"N": _hex(RFC5054_1024_N), "g": 2, "hash": "sha1", **vector}


def handshake(code: str, context: dict[str, object]) -> dict[str, str]:
    group = pake.GROUP
    password = pake.password_for_code(code)
    x = group.private_key(COMMISSIONING_ID, password, SALT)
    host = pake.ServerExchange.begin(
        identity=COMMISSIONING_ID,
        password=pake.password_for_code(CODE),
        salt=SALT,
        b=B_PRIVATE,
    )
    A = group.client_public(A_PRIVATE)
    u = group.scrambler(A, host.B)
    S = group.client_secret(host.B, x, A_PRIVATE, u)
    K = pake.session_key(S)
    encoded_context = pake.context_bytes(context)
    transcript = pake.transcript_hash(encoded_context, SALT, A, host.B)
    keys = pake.confirmation_keys(K, transcript)
    client_proof = pake.client_proof(keys, transcript)
    return {
        "password": password,
        "x": _hex(x),
        "A": _hex(A, group.width),
        "B": _hex(host.B, group.width),
        "u": _hex(u),
        "S": _hex(S, group.width),
        "K": K.hex(),
        "context_bytes": encoded_context.decode("ascii"),
        "transcript_hash": transcript.hex(),
        "client_confirm_key": keys.client.hex(),
        "server_confirm_key": keys.server.hex(),
        "client_proof": client_proof.hex(),
        "server_proof": pake.server_proof(keys, transcript, client_proof).hex(),
    }


def build() -> dict[str, object]:
    group = pake.GROUP
    rfc = check_rfc5054()
    honest = handshake(CODE, CONTEXT)
    host = pake.ServerExchange.begin(
        identity=COMMISSIONING_ID,
        password=pake.password_for_code(CODE),
        salt=SALT,
        b=B_PRIVATE,
    )
    A_encoded = bytes.fromhex(honest["A"])
    # The Host's own answer must be the one the phone expects, or the vector
    # describes a handshake nobody can finish.
    server_proof = host.finish(
        A_encoded=A_encoded,
        context=honest["context_bytes"].encode("ascii"),
        presented_proof=bytes.fromhex(honest["client_proof"]),
    )
    if server_proof.hex() != honest["server_proof"]:
        raise SystemExit("the Host and the phone disagree on the server proof")
    if group.verifier(COMMISSIONING_ID, honest["password"], SALT) != host.v:
        raise SystemExit("verifier mismatch")
    if honest["password"] != hashlib.sha256(CODE.encode()).hexdigest():
        raise SystemExit("password is not the stored secret_hash")

    wrong_code = handshake("40718257", CONTEXT)
    other_tls = handshake(
        CODE,
        {**CONTEXT, "tls_spki_fingerprint": "sha256:" + _stand_in("a relay's tls key")},
    )
    other_transport = handshake(CODE, {**CONTEXT, "transport": "lan"})
    other_controller = handshake(
        CODE,
        {**CONTEXT, "controller_public_key_sha256": _stand_in("another controller key")},
    )
    proof_refusals = []
    for name, variant in (
        ("the phone typed a different code", wrong_code),
        ("the phone pinned a different TLS key", other_tls),
        ("the phone is talking over the other transport", other_transport),
        ("the phone bound a different controller key", other_controller),
    ):
        proof_refusals.append({"name": name, "A": variant["A"], "client_proof": variant["client_proof"]})
        try:
            host.finish(
                A_encoded=bytes.fromhex(variant["A"]),
                context=honest["context_bytes"].encode("ascii"),
                presented_proof=bytes.fromhex(variant["client_proof"]),
            )
        except pake.PakeRejected:
            continue
        raise SystemExit(f"the Host accepted: {name}")

    width = group.width
    public_value_refusals = [
        {"name": "zero", "value": _hex(0, width)},
        {"name": "N", "value": _hex(group.N, width)},
        {"name": "one byte short", "value": _hex(group.client_public(A_PRIVATE), width)[2:]},
        {"name": "one byte long", "value": "00" + _hex(group.client_public(A_PRIVATE), width)},
    ]
    for refusal in public_value_refusals:
        try:
            group.public_value(bytes.fromhex(refusal["value"]))
        except pake.PakeRejected:
            continue
        raise SystemExit(f"accepted a public value that is {refusal['name']}")

    return {
        "contract_version": "1",
        "suite": pake.SUITE,
        "about": (
            "One Host claim handshake with every intermediate value, then the inputs "
            "both sides must refuse. Integers are big-endian hex; A, B and S are padded "
            "to the group width, x and u are not. On the wire every byte string is "
            "unpadded base64url. Regenerate with generate_host_claim_srp6a.py."
        ),
        "group": {
            "N": _hex(group.N, width),
            "g": group.g,
            "hash": group.hash_name,
            "width": width,
            "k": _hex(group.k),
        },
        "labels": {
            "transcript": pake.TRANSCRIPT_LABEL.hex(),
            "client_confirm": pake.CLIENT_CONFIRM_INFO.decode("ascii"),
            "server_confirm": pake.SERVER_CONFIRM_INFO.decode("ascii"),
        },
        "rfc5054_appendix_b": rfc,
        "handshake": {
            "code": CODE,
            "identity": COMMISSIONING_ID,
            "salt": SALT.hex(),
            "a": _hex(A_PRIVATE, 32),
            "b": _hex(B_PRIVATE, 32),
            "context": CONTEXT,
            "v": _hex(host.v, width),
            **honest,
        },
        "host_refuses_proof": proof_refusals,
        "both_refuse_public_value": public_value_refusals,
    }


def render() -> str:
    return json.dumps(build(), indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    text = render()
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_text(encoding="utf-8") != text:
            print(f"{OUTPUT.relative_to(ADMIN_ROOT)} is stale; regenerate it", file=sys.stderr)
            return 1
        return 0
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
