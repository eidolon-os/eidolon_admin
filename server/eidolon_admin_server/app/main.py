"""FastAPI composition root for the Eidolon Admin control plane."""

from __future__ import annotations

from contextlib import asynccontextmanager
import pwd

import logging

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .benchmarks import router as benchmarks_router
from .channel.router import router as channel_router
from .client_web.router import router as client_web_router
from .configs.router import router as configs_router
from .control_plane.router import router as control_plane_router
from .control_plane.service import ControlPlaneService
from .mission_control.router import router as mission_control_router
from .host_services.client import HostServiceClient
from .host_services.router import router as host_services_router
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
from .tools.mobile.service import DEFAULT_CLIENT_ROOT as MOBILE_CLIENT_ROOT
from .workstation import esp32_capability, mobile_capability
from ..lifecycle_workflow.capability import RemovalCapabilityBroker


def create_app(
    config: GatewayConfig | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    cfg = config or load_gateway_config(settings.services_file)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        broker = None
        try:
            if settings.removal_capability_socket is not None:
                try:
                    workflow_uid = pwd.getpwnam(
                        settings.removal_capability_workflow_user
                    ).pw_uid
                except KeyError as exc:
                    raise RuntimeError(
                        "Lifecycle Workflow service account is unavailable"
                    ) from exc
                broker = RemovalCapabilityBroker(
                    socket_path=settings.removal_capability_socket,
                    allowed_workflow_uid=workflow_uid,
                    service=app.state.control_plane,
                )
                await broker.start()
            yield
        finally:
            if broker is not None:
                await broker.close()
            await app.state.control_plane.close()
            await app.state.host_services.close()
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
    app.state.host_services = HostServiceClient(
        base_url=settings.system_directory_url,
        timeout_seconds=settings.authority_timeout_seconds,
        uds_path=settings.system_directory_uds,
        # The pooled client speaks TCP; a Unix socket needs its own transport.
        client=None if settings.system_directory_uds else app.state.http_client,
    )
    app.state.supervisor_client = SupervisorClient(settings.supervisor_socket)
    app.state.supervisor_configs = ConfigStore(
        settings.supervisor_available_dir,
        settings.supervisor_enabled_dir,
    )
    # Workstation tools are optional developer conveniences. A product Host has
    # no serial port, ESP-IDF or Flutter SDK, and their absence must not take
    # the control plane down with them.
    esp32 = esp32_capability(settings.esp32_tools_file)
    mobile = mobile_capability(MOBILE_CLIENT_ROOT)
    app.state.workstation_capabilities = (esp32, mobile)
    app.state.esp32_tools = (
        Esp32ToolService(
            catalog_file=settings.esp32_tools_file,
            jobs_root=settings.state_dir / "esp32-tools" / "jobs",
        )
        if esp32.available
        else None
    )
    app.state.mobile_tools = (
        MobileToolService(jobs_root=settings.state_dir / "mobile-tools" / "jobs")
        if mobile.available
        else None
    )
    for capability in app.state.workstation_capabilities:
        if not capability.available:
            logging.getLogger(__name__).info(
                "workstation tool unavailable on this Host: %s (%s)",
                capability.name,
                capability.detail,
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
    app.include_router(host_services_router, prefix="/api")
    app.include_router(supervisor_router, prefix="/api")
    app.include_router(channel_router, prefix="/api")
    app.include_router(client_web_router, prefix="/api")
    app.include_router(configs_router, prefix="/api")
    app.include_router(control_plane_router, prefix="/api")
    # Read-only and second-hand: it asks the same authorities every other
    # surface here asks. Registered rather than merely present, because a
    # module nothing routes to is one nobody can tell is broken.
    app.include_router(mission_control_router, prefix="/api")
    app.include_router(system_health_router, prefix="/api")
    if esp32.available:
        app.include_router(esp32_tools_router, prefix="/api")
    if mobile.available:
        app.include_router(mobile_tools_router, prefix="/api")
    # Catch-all proxy remains last so the exact service catalog route wins.
    app.include_router(gateway_router, prefix="/api")
    return app


app = create_app()
