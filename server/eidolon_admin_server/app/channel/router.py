"""Channel module — process-only integration.

Channel (the LiveKit voice worker) has no HTTP / NATS admin surface. We do
NOT modify the channel project. All we expose here is:

  GET /api/channel/config     parsed deploy/.livekit-channel.env (secrets masked)

Process status and logs are reached through the existing supervisor endpoints
(/api/supervisor/programs/channel:channel-worker, /api/supervisor/.../logs).
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter

from ..common.dotenv_view import read_dotenv_view

router = APIRouter(prefix="/channel", tags=["channel"])

_DEFAULT_ENV_PATH = Path(
    "/Users/manson/ai/eidolon/eidolon_channel/deploy/.livekit-channel.env"
)


def _env_path() -> Path:
    return Path(os.environ.get("EIDOLON_CHANNEL_ENV_FILE") or _DEFAULT_ENV_PATH).expanduser()


@router.get("/config")
def get_channel_config() -> dict:
    return read_dotenv_view(
        _env_path(),
        missing_hint="copy deploy/livekit-channel.env.template to deploy/.livekit-channel.env",
    )
