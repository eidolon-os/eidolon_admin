"""Entry point for `eidolon-admin` console script."""

from __future__ import annotations

import uvicorn

from .main import app
from .settings import get_settings, load_gateway_config


def main() -> None:
    settings = get_settings()
    cfg = load_gateway_config(settings.services_file)
    uvicorn.run(
        app,
        host=cfg.admin.host,
        port=cfg.admin.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
