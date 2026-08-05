"""Minimal local product API.

Only public read routes exist in Phase 0. Mutations are intentionally absent
until Controller authentication and Owner-scope enforcement are implemented.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, status

from ..bootstrap.control import BootstrapControlClient, BootstrapControlError
from .config import LocalApiSettings, load_local_api_settings


def create_app(settings: LocalApiSettings | None = None) -> FastAPI:
    resolved = settings or load_local_api_settings()
    app = FastAPI(
        title="Eidolon Local API",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
    )
    client = BootstrapControlClient(resolved.bootstrap.control_socket)

    async def request_bootstrap(operation: str) -> dict:
        try:
            return await client.request(operation)
        except (BootstrapControlError, ConnectionError, FileNotFoundError, OSError) as exc:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "bootstrap control plane unavailable",
            ) from exc

    @app.get("/healthz")
    async def health() -> dict:
        result = await request_bootstrap("health")
        return {"status": "ok", "bootstrap": result["status"]}

    @app.get("/api/local/v1/descriptor")
    async def descriptor() -> dict:
        return await request_bootstrap("descriptor")

    @app.get("/api/local/v1/system/state")
    async def system_state() -> dict:
        result = await request_bootstrap("health")
        return {
            "status": result["status"],
            "mode": result["mode"],
            "state": result["state"],
        }

    return app
