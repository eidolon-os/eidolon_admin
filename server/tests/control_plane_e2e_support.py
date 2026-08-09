"""Isolated process support for the real control-plane E2E test."""

from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException

from eidolon_admin_server.app.control_plane.clients import (
    DATA_CONTRACT,
    DATA_RUNTIME_CONTRACT,
    DATA_WORKSPACE_CONTRACT,
    HUB_CONTRACT,
    KERNEL_CONTRACT,
)


def create_directory_app() -> FastAPI:
    app = FastAPI()
    endpoints = {
        ("data", "companion-authority.http"): (
            os.environ["EIDOLON_E2E_DATA_URL"],
            DATA_CONTRACT,
        ),
        ("data", "companion-runtime-authority.http"): (
            os.environ["EIDOLON_E2E_DATA_URL"],
            DATA_RUNTIME_CONTRACT,
        ),
        ("data-workspace", "workspace-authority.http"): (
            os.environ["EIDOLON_E2E_DATA_WORKSPACE_URL"],
            DATA_WORKSPACE_CONTRACT,
        ),
        ("hub", "device-authority.http"): (
            os.environ["EIDOLON_E2E_HUB_URL"],
            HUB_CONTRACT,
        ),
        ("kernel", "device-mount.http"): (
            os.environ["EIDOLON_E2E_KERNEL_URL"],
            KERNEL_CONTRACT,
        ),
    }

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/system/v1/services/{service_id}/endpoints/{endpoint_id}")
    async def endpoint(service_id: str, endpoint_id: str) -> dict[str, str]:
        value = endpoints.get((service_id, endpoint_id))
        if value is None:
            raise HTTPException(status_code=404, detail="endpoint not registered")
        address, contract = value
        return {
            "operation": "system.service-endpoint",
            "service_id": service_id,
            "endpoint_id": endpoint_id,
            "protocol": "http",
            "address": address,
            "contract": contract,
        }

    return app
