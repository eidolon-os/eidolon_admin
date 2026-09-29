"""Channel module — process-only integration.

Channel (the LiveKit voice worker) has no HTTP / NATS admin surface. We do
NOT modify the channel project. All we expose here is:

  GET /api/channel/config     parsed deploy/.livekit-channel.env (secrets masked)

Process status is part of /api/overview/services; restarting the worker goes
through Host services (/api/host/services/channel/restart → eidolond).
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter

from ..common.dotenv_view import read_dotenv_view

router = APIRouter(prefix="/channel", tags=["channel"])

def _default_env_path() -> Path:
    from ..settings import default_eidolon_root

    return default_eidolon_root() / "eidolon_channel" / "config" / ".env"


def _env_path() -> Path:
    return Path(os.environ.get("EIDOLON_CHANNEL_ENV_FILE") or _default_env_path()).expanduser()


@router.get("/config")
def get_channel_config() -> dict:
    return read_dotenv_view(
        _env_path(),
        missing_hint="copy deploy/livekit-channel.env.template to config/.env",
    )
