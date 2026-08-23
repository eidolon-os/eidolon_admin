"""Configuration for the unprivileged local product API."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse

from eidolon_sdk.device_foundation.v1 import (
    AuthorityLocator,
    OwnerDomainDescriptor,
    OwnerDomainTrustAnchor,
)

from ..bootstrap.config import BootstrapSettings, load_bootstrap_settings


@dataclass(frozen=True, slots=True)
class VerifiedOwnerDomainOnboardingTarget:
    """Owner trust bundle derived from installation-owned public material."""

    owner_domain_id: str
    descriptor_uri: str
    descriptor: OwnerDomainDescriptor
    owner_root_certificate_path: Path
    authority_signing_certificate_path: Path


@dataclass(frozen=True, slots=True)
class LocalApiSettings:
    bootstrap: BootstrapSettings
    host: str = "127.0.0.1"
    port: int = 9002
    session_ttl_seconds: int = 900
    admin_base_url: str = "http://127.0.0.1:9000"
    admin_service_token: str = ""
    admin_timeout_seconds: float = 5.0
    lifecycle_workflow_socket: Path = Path("/run/eidolon-lifecycle/workflow.sock")
    device_onboarding_target: VerifiedOwnerDomainOnboardingTarget | None = None


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
        lifecycle_workflow_socket=_absolute_path(
            env.get(
                "EIDOLON_LOCAL_API_LIFECYCLE_WORKFLOW_SOCKET",
                "/run/eidolon-lifecycle/workflow.sock",
            ),
            name="EIDOLON_LOCAL_API_LIFECYCLE_WORKFLOW_SOCKET",
        ),
        device_onboarding_target=target,
    )


def _absolute_path(value: str, *, name: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{name} must be absolute")
    return path


def _load_hub_onboarding_target(
    env: Mapping[str, str],
    *,
    production: bool,
) -> VerifiedOwnerDomainOnboardingTarget | None:
    values = {
        "owner_domain_id": env.get(
            "EIDOLON_LOCAL_API_OWNER_DOMAIN_ID", ""
        ).strip(),
        "descriptor_uri": env.get(
            "EIDOLON_LOCAL_API_OWNER_DOMAIN_DESCRIPTOR_URI", ""
        ).strip(),
        "descriptor": env.get(
            "EIDOLON_LOCAL_API_OWNER_DOMAIN_DESCRIPTOR", ""
        ).strip(),
        "owner_root_certificate": env.get(
            "EIDOLON_LOCAL_API_OWNER_ROOT_CERTIFICATE", ""
        ).strip(),
        "authority_signing_certificate": env.get(
            "EIDOLON_LOCAL_API_AUTHORITY_SIGNING_CERTIFICATE", ""
        ).strip(),
    }
    configured = [bool(value) for value in values.values()]
    if not any(configured):
        return None
    if not all(configured):
        raise ValueError(
            "Owner Domain onboarding requires its id, descriptor URI, signed "
            "descriptor, root certificate and authority signing certificate together"
        )
    owner_domain_id = values["owner_domain_id"]
    if not 1 <= len(owner_domain_id) <= 128:
        raise ValueError("EIDOLON_LOCAL_API_OWNER_DOMAIN_ID is invalid")
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
            "EIDOLON_LOCAL_API_OWNER_DOMAIN_DESCRIPTOR_URI must be the plain "
            "HTTPS descriptor URI"
        )
    paths = {
        name: Path(values[name]).expanduser()
        for name in (
            "descriptor",
            "owner_root_certificate",
            "authority_signing_certificate",
        )
    }
    if production and any(not path.is_absolute() for path in paths.values()):
        raise ValueError("Owner Domain trust paths must be absolute in production")
    try:
        descriptor = OwnerDomainDescriptor.model_validate_json(
            paths["descriptor"].read_text(encoding="utf-8")
        )
        anchor = OwnerDomainTrustAnchor(
            owner_domain_id=owner_domain_id,
            owner_root_certificate_pem=paths["owner_root_certificate"].read_text(
                encoding="ascii"
            ),
            authority_signing_certificate_pem=paths[
                "authority_signing_certificate"
            ].read_text(encoding="ascii"),
            trust_epoch=1,
        )
        locator = AuthorityLocator(anchor)
        locator.accept(descriptor, now=datetime.now(UTC))
    except (OSError, ValueError) as exc:
        raise ValueError("Owner Domain trust bundle is invalid") from exc
    return VerifiedOwnerDomainOnboardingTarget(
        owner_domain_id=owner_domain_id,
        descriptor_uri=descriptor_uri,
        descriptor=descriptor,
        owner_root_certificate_path=paths["owner_root_certificate"].resolve(),
        authority_signing_certificate_path=paths[
            "authority_signing_certificate"
        ].resolve(),
    )
