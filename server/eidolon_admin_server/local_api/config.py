"""Configuration for the unprivileged local product API."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from ..bootstrap.config import BootstrapSettings, load_bootstrap_settings


@dataclass(frozen=True, slots=True)
class LocalApiSettings:
    bootstrap: BootstrapSettings
    host: str = "127.0.0.1"
    port: int = 9002
    session_ttl_seconds: int = 900


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
    return LocalApiSettings(
        bootstrap=load_bootstrap_settings(env),
        host=env.get("EIDOLON_LOCAL_API_HOST", "127.0.0.1"),
        port=port,
        session_ttl_seconds=session_ttl,
    )
