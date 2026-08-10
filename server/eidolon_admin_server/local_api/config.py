"""Configuration for the unprivileged local product API."""

from __future__ import annotations

import os
import re
from base64 import urlsafe_b64encode
from datetime import UTC, datetime
from dataclasses import dataclass
from hashlib import sha256
from ipaddress import ip_address
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse

from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from ..bootstrap.config import BootstrapSettings, load_bootstrap_settings


@dataclass(frozen=True, slots=True)
class VerifiedHubOnboardingTarget:
    """Hub product identity derived from installation-owned configuration."""

    hub_id: str
    descriptor_uri: str
    tls_spki_fingerprint: str
    tls_certificate_path: Path


@dataclass(frozen=True, slots=True)
class LocalApiSettings:
    bootstrap: BootstrapSettings
    host: str = "127.0.0.1"
    port: int = 9002
    session_ttl_seconds: int = 900
    admin_base_url: str = "http://127.0.0.1:9000"
    admin_service_token: str = ""
    admin_timeout_seconds: float = 5.0
    device_onboarding_target: VerifiedHubOnboardingTarget | None = None


def load_local_api_settings(
    environ: Mapping[str, str] | None = None,
) -> LocalApiSettings:
    env = os.environ if environ is None else environ
    try:
        port = int(env.get("EIDOLON_LOCAL_API_PORT", "9002"))
    except ValueError as exc:
        raise ValueError("EIDOLON_LOCAL_API_PORT must be an integer") from exc
    if not 1 <= port <= 65535:
        raise ValueError("EIDOLON_LOCAL_API_PORT must be between 1 and 65535")
    try:
        session_ttl = int(env.get("EIDOLON_LOCAL_API_SESSION_TTL_SECONDS", "900"))
    except ValueError as exc:
        raise ValueError(
            "EIDOLON_LOCAL_API_SESSION_TTL_SECONDS must be an integer"
        ) from exc
    if not 60 <= session_ttl <= 86400:
        raise ValueError(
            "EIDOLON_LOCAL_API_SESSION_TTL_SECONDS must be between 60 and 86400"
        )
    admin_base_url = env.get(
        "EIDOLON_LOCAL_API_ADMIN_BASE_URL", "http://127.0.0.1:9000"
    ).rstrip("/")
    parsed_admin = urlparse(admin_base_url)
    if (
        parsed_admin.scheme != "http"
        or parsed_admin.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed_admin.username is not None
        or parsed_admin.password is not None
        or parsed_admin.path not in {"", "/"}
        or parsed_admin.query
        or parsed_admin.fragment
    ):
        raise ValueError(
            "EIDOLON_LOCAL_API_ADMIN_BASE_URL must be a loopback HTTP origin"
        )
    try:
        admin_timeout = float(env.get("EIDOLON_LOCAL_API_ADMIN_TIMEOUT_SECONDS", "5"))
    except ValueError as exc:
        raise ValueError(
            "EIDOLON_LOCAL_API_ADMIN_TIMEOUT_SECONDS must be numeric"
        ) from exc
    if not 0 < admin_timeout <= 30:
        raise ValueError(
            "EIDOLON_LOCAL_API_ADMIN_TIMEOUT_SECONDS must be between 0 and 30"
        )
    bootstrap = load_bootstrap_settings(env)
    target = _load_hub_onboarding_target(
        env,
        production=bootstrap.mode.value == "production",
    )
    return LocalApiSettings(
        bootstrap=bootstrap,
        host=env.get("EIDOLON_LOCAL_API_HOST", "127.0.0.1"),
        port=port,
        session_ttl_seconds=session_ttl,
        admin_base_url=admin_base_url,
        admin_service_token=env.get("EIDOLON_LOCAL_API_ADMIN_SERVICE_TOKEN", ""),
        admin_timeout_seconds=admin_timeout,
        device_onboarding_target=target,
    )


def _load_hub_onboarding_target(
    env: Mapping[str, str],
    *,
    production: bool,
) -> VerifiedHubOnboardingTarget | None:
    values = {
        "hub_id": env.get("EIDOLON_LOCAL_API_HUB_ID", "").strip(),
        "descriptor_uri": env.get(
            "EIDOLON_LOCAL_API_HUB_DESCRIPTOR_URI", ""
        ).strip(),
        "certificate": env.get(
            "EIDOLON_LOCAL_API_HUB_TLS_CERTIFICATE", ""
        ).strip(),
    }
    configured = [bool(value) for value in values.values()]
    if not any(configured):
        return None
    if not all(configured):
        raise ValueError(
            "Hub onboarding requires EIDOLON_LOCAL_API_HUB_ID, "
            "EIDOLON_LOCAL_API_HUB_DESCRIPTOR_URI and "
            "EIDOLON_LOCAL_API_HUB_TLS_CERTIFICATE together"
        )
    hub_id = values["hub_id"]
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", hub_id) is None:
        raise ValueError("EIDOLON_LOCAL_API_HUB_ID is invalid")
    descriptor_uri = values["descriptor_uri"]
    parsed = urlparse(descriptor_uri)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != "/api/device-onboarding/v1/descriptor"
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "EIDOLON_LOCAL_API_HUB_DESCRIPTOR_URI must be the plain HTTPS "
            "Hub v1 descriptor URI"
        )
    certificate_path = Path(values["certificate"]).expanduser()
    if production and not certificate_path.is_absolute():
        raise ValueError(
            "EIDOLON_LOCAL_API_HUB_TLS_CERTIFICATE must be absolute in production"
        )
    try:
        certificate = x509.load_pem_x509_certificate(certificate_path.read_bytes())
    except (OSError, ValueError) as exc:
        raise ValueError(
            "EIDOLON_LOCAL_API_HUB_TLS_CERTIFICATE must contain the Hub TLS leaf certificate"
        ) from exc
    now = datetime.now(UTC)
    if not certificate.not_valid_before_utc <= now <= certificate.not_valid_after_utc:
        raise ValueError("Hub TLS leaf certificate is not currently valid")
    _verify_certificate_hostname(certificate, parsed.hostname)
    spki = certificate.public_key().public_bytes(
        Encoding.DER,
        PublicFormat.SubjectPublicKeyInfo,
    )
    return VerifiedHubOnboardingTarget(
        hub_id=hub_id,
        descriptor_uri=descriptor_uri,
        tls_spki_fingerprint=(
            "sha256:"
            + urlsafe_b64encode(sha256(spki).digest()).rstrip(b"=").decode()
        ),
        tls_certificate_path=certificate_path.resolve(),
    )


def _verify_certificate_hostname(certificate: x509.Certificate, hostname: str) -> None:
    try:
        san = certificate.extensions.get_extension_for_class(
            x509.SubjectAlternativeName
        ).value
    except x509.ExtensionNotFound as exc:
        raise ValueError("Hub TLS leaf certificate must contain subjectAltName") from exc
    try:
        expected_ip = ip_address(hostname)
    except ValueError:
        expected_dns = hostname.rstrip(".").lower()
        dns_names = {
            value.rstrip(".").lower()
            for value in san.get_values_for_type(x509.DNSName)
        }
        if expected_dns not in dns_names:
            raise ValueError(
                "Hub descriptor hostname is not present in the TLS certificate SAN"
            )
        return
    ip_names = set(san.get_values_for_type(x509.IPAddress))
    if expected_ip not in ip_names:
        raise ValueError(
            "Hub descriptor IP is not present in the TLS certificate SAN"
        )
