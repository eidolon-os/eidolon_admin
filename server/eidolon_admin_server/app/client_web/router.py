"""Eidolon client-web — process-only integration.

Next.js dev server connecting browsers to hub's LiveKit room and
hub's /api/config token endpoint. No admin HTTP surface on its own; we
supervise the process and surface its dotenv config (NEXT_PUBLIC_* etc).
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter

from ..common.dotenv_view import read_dotenv_view

router = APIRouter(prefix="/client-web", tags=["client-web"])

def _default_env_path() -> Path:
    from ..settings import default_eidolon_root

    return default_eidolon_root() / "eidolon_client_web" / ".env.local"


def _env_path() -> Path:
    return Path(os.environ.get("EIDOLON_CLIENT_WEB_ENV_FILE") or _default_env_path()).expanduser()


@router.get("/config")
def get_client_web_config() -> dict:
    return read_dotenv_view(
        _env_path(),
        missing_hint="copy .env.local.example to .env.local",
    )
