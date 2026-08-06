"""Console entrypoint for the local product API."""

from __future__ import annotations

import uvicorn

from .app import create_app
from .config import load_local_api_settings


def main() -> None:
    settings = load_local_api_settings()
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_level="info",
        ssl_certfile=str(settings.bootstrap.commissioning_tls_pem_path),
        ssl_keyfile=str(settings.bootstrap.commissioning_tls_pem_path),
    )


if __name__ == "__main__":
    main()
