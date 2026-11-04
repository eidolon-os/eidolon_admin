"""SRP-6a for the Host claim: the Setup code establishes trust instead of riding inside it.

Until this, the phone read a Host's endpoint document, pinned the TLS key that
document named, and then sent the eight-digit Setup code in plaintext inside
that TLS session for the Host to hash and compare. The document is signed by a
key it carries itself, so first contact was unauthenticated: anything that could
answer as a Host — over BLE or on the LAN — received the code and could relay
it to the real one (ADR-0006, open item; ADR-0008 records this design).

Here the code never travels. Both sides run SRP-6a (RFC 5054, 3072-bit group,
SHA-512) keyed by it, and both confirmations are bound to a transcript that
names the Host's long-term key, its TLS key, the window, the reset epoch and the
phone's own key. A party that does not know the code cannot finish the exchange,
cannot test guesses offline, and cannot sit between a phone and the real Host,
because its TLS key would be in the transcript the real Host refuses.

Pure functions only: no I/O, no clock, no randomness of its own. The golden
vector in ``contracts/bootstrap/v1/golden/host-claim-srp6a-v1.json`` pins every
intermediate value, and the phone's Dart implementation is held to it.

The SRP password is the lowercase hex SHA-256 of the code — exactly the
``secret_hash`` this Host already stores — so no window needs migrating and a
Host can be rolled back without touching its database.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import struct
from dataclasses import dataclass
from typing import Any

SUITE = "srp6a-rfc5054-3072-sha512-v1"
TRANSCRIPT_LABEL = b"eidolon-host-claim-pake-v1\x00"
CLIENT_CONFIRM_INFO = b"eidolon-host-claim-pake-v1 client confirm"
SERVER_CONFIRM_INFO = b"eidolon-host-claim-pake-v1 server confirm"
SALT_BYTES = 16
EPHEMERAL_BYTES = 32


class PakeRejected(ValueError):
    """A value the protocol must refuse: a public value that is zero mod N,
    u = 0, a value of the wrong width, or a proof that does not verify."""


class SrpGroup:
    """SRP-6a over one group and hash, exactly as RFC 5054 §2.5–2.6 computes it.

    Parameterised only so that RFC 5054 Appendix B (1024-bit, SHA-1) can check
    the arithmetic; the claim itself uses ``GROUP`` and nothing else.
    """

    def __init__(self, N: int, g: int, hash_name: str) -> None:
        self.N = N
        self.g = g
        self.hash_name = hash_name
        self.width = (N.bit_length() + 7) // 8
        self.k = self.as_int(self.H(self.pad(N), self.pad(g)))

    def H(self, *parts: bytes) -> bytes:
        digest = hashlib.new(self.hash_name)
        for part in parts:
            digest.update(part)
        return digest.digest()

    def pad(self, value: int) -> bytes:
        return value.to_bytes(self.width, "big")

    @staticmethod
    def as_int(value: bytes) -> int:
        return int.from_bytes(value, "big")

    def private_key(self, identity: str, password: str, salt: bytes) -> int:
        """x = H(s || H(I ":" P))."""

        return self.as_int(self.H(salt, self.H(f"{identity}:{password}".encode("utf-8"))))

    def verifier(self, identity: str, password: str, salt: bytes) -> int:
        return pow(self.g, self.private_key(identity, password, salt), self.N)

    def public_value(self, encoded: bytes) -> int:
        """A peer's A or B: exactly the group's width, and not zero mod N."""

        if len(encoded) != self.width:
            raise PakeRejected(f"public value must be {self.width} bytes")
        value = self.as_int(encoded)
        if value % self.N == 0:
            raise PakeRejected("public value is zero mod N")
        return value

    def scrambler(self, A: int, B: int) -> int:
        u = self.as_int(self.H(self.pad(A), self.pad(B)))
        if u == 0:
            raise PakeRejected("u is zero")
        return u

    def server_public(self, v: int, b: int) -> int:
        return (self.k * v + pow(self.g, b, self.N)) % self.N

    def client_public(self, a: int) -> int:
        return pow(self.g, a, self.N)

    def server_secret(self, A: int, v: int, u: int, b: int) -> int:
        return pow(A * pow(v, u, self.N) % self.N, b, self.N)

    def client_secret(self, B: int, x: int, a: int, u: int) -> int:
        base = (B - self.k * pow(self.g, x, self.N)) % self.N
        return pow(base, a + u * x, self.N)


# RFC 5054 Appendix A, 3072-bit group; the same N the devices use for Security 2.
GROUP = SrpGroup(
    N=int(
        "ffffffffffffffffc90fdaa22168c234c4c6628b80dc1cd129024e088a67cc74"
        "020bbea63b139b22514a08798e3404ddef9519b3cd3a431b302b0a6df25f1437"
        "4fe1356d6d51c245e485b576625e7ec6f44c42e9a637ed6b0bff5cb6f406b7ed"
        "ee386bfb5a899fa5ae9f24117c4b1fe649286651ece45b3dc2007cb8a163bf05"
        "98da48361c55d39a69163fa8fd24cf5f83655d23dca3ad961c62f356208552bb"
        "9ed529077096966d670c354e4abc9804f1746c08ca18217c32905e462e36ce3b"
        "e39e772c180e86039b2783a2ec07a28fb5c55df06f4c52c9de2bcbf695581718"
        "3995497cea956ae515d2261898fa051015728e5a8aaac42dad33170d04507a33"
        "a85521abdf1cba64ecfb850458dbef0a8aea71575d060c7db3970f85a6e1e4c7"
        "abf5ae8cdb0933d71e8c94e04a25619dcee3d2261ad2ee6bf12ffa06d98a0864"
        "d87602733ec86a64521f2b18177b200cbbe117577a615d6c770988c0bad946e2"
        "08e24fa074e5ab3143db5bfce0fd108e4b82d120a93ad2caffffffffffffffff",
        16,
    ),
    g=5,
    hash_name="sha512",
)


def password_for_code(code: str) -> str:
    """The SRP password for a Setup code: what the Host stores as ``secret_hash``."""

    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def session_key(S: int) -> bytes:
    return GROUP.H(GROUP.pad(S))


def context_bytes(context: dict[str, Any]) -> bytes:
    """Canonical JSON: sorted keys, no whitespace, ASCII only."""

    return json.dumps(context, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "ascii"
    )


def transcript_hash(context: bytes, salt: bytes, A: int, B: int) -> bytes:
    return GROUP.H(
        TRANSCRIPT_LABEL,
        struct.pack(">I", len(context)),
        context,
        struct.pack(">I", len(salt)),
        salt,
        GROUP.pad(A),
        GROUP.pad(B),
    )


def _hkdf_expand(prk: bytes, info: bytes, length: int) -> bytes:
    output = b""
    block = b""
    counter = 1
    while len(output) < length:
        block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha512).digest()
        output += block
        counter += 1
    return output[:length]


@dataclass(frozen=True)
class ConfirmationKeys:
    client: bytes
    server: bytes


def confirmation_keys(K: bytes, transcript: bytes) -> ConfirmationKeys:
    """HKDF-SHA512 (RFC 5869) with the transcript hash as salt and K as input."""

    prk = hmac.new(transcript, K, hashlib.sha512).digest()
    return ConfirmationKeys(
        client=_hkdf_expand(prk, CLIENT_CONFIRM_INFO, 64),
        server=_hkdf_expand(prk, SERVER_CONFIRM_INFO, 64),
    )


def client_proof(keys: ConfirmationKeys, transcript: bytes) -> bytes:
    return hmac.new(keys.client, transcript, hashlib.sha512).digest()


def server_proof(keys: ConfirmationKeys, transcript: bytes, client_proof_value: bytes) -> bytes:
    return hmac.new(keys.server, transcript + client_proof_value, hashlib.sha512).digest()


@dataclass(frozen=True)
class ServerExchange:
    """The Host's half of one handshake, held between pake.start and pake.claim."""

    salt: bytes
    v: int
    b: int
    B: int

    @classmethod
    def begin(cls, *, identity: str, password: str, salt: bytes, b: int) -> "ServerExchange":
        v = GROUP.verifier(identity, password, salt)
        return cls(salt=salt, v=v, b=b, B=GROUP.server_public(v, b))

    @property
    def B_encoded(self) -> bytes:
        return GROUP.pad(self.B)

    def finish(self, *, A_encoded: bytes, context: bytes, presented_proof: bytes) -> bytes:
        """Verify the phone's proof and return the Host's. Raises PakeRejected."""

        A = GROUP.public_value(A_encoded)
        u = GROUP.scrambler(A, self.B)
        K = session_key(GROUP.server_secret(A, self.v, u, self.b))
        transcript = transcript_hash(context, self.salt, A, self.B)
        keys = confirmation_keys(K, transcript)
        expected = client_proof(keys, transcript)
        if not hmac.compare_digest(expected, presented_proof):
            raise PakeRejected("client proof does not verify")
        return server_proof(keys, transcript, presented_proof)
