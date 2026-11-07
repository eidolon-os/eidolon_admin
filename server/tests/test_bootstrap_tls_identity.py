"""The Local API certificate wraps the Host's transport key for its lifetime.

Phones pin that key, vouched for by the Host identity, so the certificate is
never renewed: one issued under the earlier ten-year policy is certified once
more under the same key, and nothing a phone holds changes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.x509.oid import NameOID

from eidolon_admin_server.bootstrap.tls_identity import (
    CommissioningTlsIdentityError,
    CommissioningTlsIdentityManager,
)

HOST = "ehost-0123456789abcdef0123"


def _certificate(path: Path) -> x509.Certificate:
    return x509.load_pem_x509_certificate(path.read_bytes())


def _issue_as_before_the_lifetime_policy(path: Path, *, start: datetime) -> None:
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, HOST)])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(start)
        .not_valid_after(start + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.UniformResourceIdentifier(f"urn:eidolon:host:{HOST}")]
            ),
            False,
        )
        .sign(key, hashes.SHA256())
    )
    path.write_bytes(
        certificate.public_bytes(serialization.Encoding.PEM)
        + key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    path.chmod(0o640)


def test_a_new_identity_is_issued_for_the_lifetime(tmp_path: Path) -> None:
    path = tmp_path / "commissioning_tls.pem"
    identity = CommissioningTlsIdentityManager(path).load(HOST)
    certificate = _certificate(path)

    issued = certificate.not_valid_before_utc
    assert certificate.not_valid_after_utc.year - issued.year == 99
    assert path.stat().st_mode & 0o777 == 0o640

    settled = path.read_bytes()
    assert CommissioningTlsIdentityManager(path).load(HOST) == identity
    assert path.read_bytes() == settled


@pytest.mark.parametrize(
    "age", [timedelta(days=1), timedelta(days=4000)], ids=["current", "expired"]
)
def test_a_ten_year_identity_is_recertified_under_its_own_key(
    tmp_path: Path, age: timedelta
) -> None:
    path = tmp_path / "commissioning_tls.pem"
    pinned = CommissioningTlsIdentityManager(path).load(HOST).spki_fingerprint
    _issue_as_before_the_lifetime_policy(path, start=datetime.now(UTC) - age)

    identity = CommissioningTlsIdentityManager(path).load(HOST)

    # The pin a phone holds is the key's, and the key did not change.
    assert identity.spki_fingerprint == pinned
    certificate = _certificate(path)
    assert (
        certificate.not_valid_after_utc.year - certificate.not_valid_before_utc.year
        == 99
    )
    assert (
        certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == HOST
    )
    assert certificate.extensions.get_extension_for_class(
        x509.SubjectAlternativeName
    ).value.get_values_for_type(x509.UniformResourceIdentifier) == [
        f"urn:eidolon:host:{HOST}"
    ]
    assert path.stat().st_mode & 0o777 == 0o640

    settled = path.read_bytes()
    CommissioningTlsIdentityManager(path).load(HOST)
    assert path.read_bytes() == settled


def test_another_hosts_identity_and_unsafe_mode_are_still_refused(
    tmp_path: Path,
) -> None:
    path = tmp_path / "commissioning_tls.pem"
    CommissioningTlsIdentityManager(path).load(HOST)

    with pytest.raises(CommissioningTlsIdentityError, match="another Host"):
        CommissioningTlsIdentityManager(path).load("ehost-ffffffffffffffffffff")

    path.chmod(0o644)
    with pytest.raises(CommissioningTlsIdentityError, match="mode 0640"):
        CommissioningTlsIdentityManager(path).load(HOST)
