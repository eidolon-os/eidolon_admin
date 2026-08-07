"""FastAPI composition root for the Eidolon Admin control plane."""

from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .benchmarks import router as benchmarks_router
from .channel.router import router as channel_router
from .client_web.router import router as client_web_router
from .configs.router import router as configs_router
from .control_plane import ControlPlaneService
from .control_plane import router as control_plane_router
from .gateway.registry import ServiceRegistry
from .gateway.router import router as gateway_router
from .routers.overview import router as overview_router
from .routers.services import router as services_router
from .settings import GatewayConfig, Settings, get_settings, load_gateway_config
from .supervisor.client import SupervisorClient
from .supervisor.config import ConfigStore
from .supervisor.router import router as supervisor_router
from .system_health import router as system_health_router
from .tools.esp32 import Esp32ToolService, router as esp32_tools_router
from .tools.mobile import MobileToolService, router as mobile_tools_router


def create_app(
    config: GatewayConfig | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    cfg = config or load_gateway_config(settings.services_file)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            yield
        finally:
            await app.state.control_plane.close()
            await app.state.http_client.aclose()

    app = FastAPI(
        title="Eidolon Admin Control Plane",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.registry = ServiceRegistry(cfg)
    app.state.gateway_config = cfg
    app.state.settings = settings
    app.state.http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(settings.authority_timeout_seconds),
        trust_env=False,
    )
    app.state.control_plane = ControlPlaneService.build(
        settings=settings,
        http_client=app.state.http_client,
    )
    app.state.supervisor_client = SupervisorClient(settings.supervisor_socket)
    app.state.supervisor_configs = ConfigStore(
        settings.supervisor_available_dir,
        settings.supervisor_enabled_dir,
    )
    app.state.esp32_tools = Esp32ToolService(
        catalog_file=settings.esp32_tools_file,
        jobs_root=settings.state_dir / "esp32-tools" / "jobs",
    )
    app.state.mobile_tools = MobileToolService(
        jobs_root=settings.state_dir / "mobile-tools" / "jobs",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.admin.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=False,
    )

    @app.get("/healthz", include_in_schema=False)
    async def process_health() -> dict[str, str]:
        """Report only that the Admin process has completed composition.

        Producer authority readiness is checked independently by deployment and
        is never collapsed into this process-local signal.
        """

        return {"status": "ready"}

    app.include_router(services_router, prefix="/api")
    app.include_router(benchmarks_router, prefix="/api")
    app.include_router(overview_router, prefix="/api")
    app.include_router(supervisor_router, prefix="/api")
    app.include_router(channel_router, prefix="/api")
    app.include_router(client_web_router, prefix="/api")
    app.include_router(configs_router, prefix="/api")
    app.include_router(control_plane_router, prefix="/api")
    app.include_router(system_health_router, prefix="/api")
    app.include_router(esp32_tools_router, prefix="/api")
    app.include_router(mobile_tools_router, prefix="/api")
    # Catch-all proxy remains last so the exact service catalog route wins.
    app.include_router(gateway_router, prefix="/api")
    return app


app = create_app()
