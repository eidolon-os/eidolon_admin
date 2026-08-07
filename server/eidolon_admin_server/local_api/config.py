"""Configuration for the unprivileged local product API."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlparse

from ..bootstrap.config import BootstrapSettings, load_bootstrap_settings


@dataclass(frozen=True, slots=True)
class LocalApiSettings:
    bootstrap: BootstrapSettings
    host: str = "127.0.0.1"
    port: int = 9002
    session_ttl_seconds: int = 900
    admin_base_url: str = "http://127.0.0.1:9000"
    admin_service_token: str = ""
    admin_timeout_seconds: float = 5.0


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
    return LocalApiSettings(
        bootstrap=load_bootstrap_settings(env),
        host=env.get("EIDOLON_LOCAL_API_HOST", "127.0.0.1"),
        port=port,
        session_ttl_seconds=session_ttl,
        admin_base_url=admin_base_url,
        admin_service_token=env.get("EIDOLON_LOCAL_API_ADMIN_SERVICE_TOKEN", ""),
        admin_timeout_seconds=admin_timeout,
    )
