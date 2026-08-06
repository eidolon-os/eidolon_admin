"""Pinned TLS endpoint identity used inside the BLE commissioning link."""

from __future__ import annotations

import base64
import hashlib
import os
import stat
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID


class CommissioningTlsIdentityError(RuntimeError):
    """Commissioning TLS material is missing, unsafe, or inconsistent."""


@dataclass(frozen=True, slots=True)
class CommissioningTlsIdentity:
    pem_path: Path
    spki_fingerprint: str


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


class CommissioningTlsIdentityManager:
    """Own a runtime TLS key; Host Ed25519 signatures bind its public SPKI."""

    def __init__(self, pem_path: Path) -> None:
        self._pem_path = pem_path
        self._identity: CommissioningTlsIdentity | None = None

    def load(self, host_id: str) -> CommissioningTlsIdentity:
        if not self._pem_path.exists():
            self._generate(host_id)
        path_stat = self._pem_path.lstat()
        if stat.S_ISLNK(path_stat.st_mode):
            raise CommissioningTlsIdentityError(
                "commissioning TLS identity must not be a symbolic link"
            )
        if (
            not stat.S_ISREG(path_stat.st_mode)
            or path_stat.st_mode & 0o777 != 0o640
        ):
            raise CommissioningTlsIdentityError(
                "commissioning TLS identity must be a regular mode 0640 file"
            )
        pem = self._pem_path.read_bytes()
        try:
            certificate = x509.load_pem_x509_certificate(pem)
            private_key = serialization.load_pem_private_key(pem, password=None)
        except ValueError as exc:
            raise CommissioningTlsIdentityError(
                "commissioning TLS identity is not valid PEM"
            ) from exc
        if not isinstance(private_key, ec.EllipticCurvePrivateKey) or not isinstance(
            private_key.curve, ec.SECP256R1
        ):
            raise CommissioningTlsIdentityError(
                "commissioning TLS private key must be P-256"
            )
        certificate_public = certificate.public_key()
        if not isinstance(certificate_public, ec.EllipticCurvePublicKey) or (
            certificate_public.public_numbers()
            != private_key.public_key().public_numbers()
        ):
            raise CommissioningTlsIdentityError(
                "commissioning TLS certificate does not match its private key"
            )
        common_names = certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
        if len(common_names) != 1 or common_names[0].value != host_id:
            raise CommissioningTlsIdentityError(
                "commissioning TLS certificate belongs to another Host"
            )
        now = datetime.now(UTC)
        if (
            certificate.not_valid_before_utc > now
            or certificate.not_valid_after_utc <= now
        ):
            raise CommissioningTlsIdentityError(
                "commissioning TLS certificate is not currently valid"
            )
        spki = certificate_public.public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        identity = CommissioningTlsIdentity(
            pem_path=self._pem_path,
            spki_fingerprint=f"sha256:{_b64url(hashlib.sha256(spki).digest())}",
        )
        self._identity = identity
        return identity

    @property
    def identity(self) -> CommissioningTlsIdentity:
        if self._identity is None:
            raise CommissioningTlsIdentityError(
                "commissioning TLS identity has not been loaded"
            )
        return self._identity

    def _generate(self, host_id: str) -> None:
        self._pem_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        private_key = ec.generate_private_key(ec.SECP256R1())
        now = datetime.now(UTC)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host_id)])
        certificate = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(subject)
            .public_key(private_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
            .add_extension(
                x509.SubjectAlternativeName(
                    [x509.UniformResourceIdentifier(f"urn:eidolon:host:{host_id}")]
                ),
                False,
            )
            .sign(private_key, hashes.SHA256())
        )
        pem = certificate.public_bytes(
            serialization.Encoding.PEM
        ) + private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".commissioning-tls-",
            dir=self._pem_path.parent,
        )
        try:
            os.fchmod(descriptor, 0o640)
            with os.fdopen(descriptor, "wb") as output:
                output.write(pem)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary_name, self._pem_path)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)
