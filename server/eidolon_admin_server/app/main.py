"""FastAPI app factory for the Eidolon admin gateway."""
from __future__ import annotations

import os

# Process-wide proxy scrub. The admin gateway only talks to localhost
# sub-projects; a system HTTP_PROXY (e.g. Clash on :7890) silently intercepts
# 127.0.0.1 calls and returns 502 with multi-second latency. We can't rely on
# NO_PROXY (handled inconsistently across libs), and `trust_env=False` only
# helps clients we construct ourselves — `mcp.streamablehttp_client`,
# `nats-py`, etc. build their own clients that read env. Strip the vars at
# import time so nothing in this process ever sees them.
for _proxy_var in (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
    "http_proxy", "https_proxy", "all_proxy",
):
    os.environ.pop(_proxy_var, None)

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .channel.router import router as channel_router
from .client_web.router import router as client_web_router
from .gateway.registry import ServiceRegistry
from .gateway.router import router as gateway_router
from .memory.nats_publisher import JetStreamPublisher
from .memory.router import router as memory_router
from .routers.overview import router as overview_router
from .routers.services import router as services_router
from .settings import GatewayConfig, Settings, get_settings, load_gateway_config
from .supervisor.client import SupervisorClient
from .supervisor.config import ConfigStore
from .supervisor.router import router as supervisor_router


def create_app(
    config: GatewayConfig | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    cfg = config or load_gateway_config(settings.services_file)

    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        # Client is created eagerly above so ASGI-transport tests work without
        # exercising lifespan; here we just ensure proper shutdown.
        try:
            yield
        finally:
            await app.state.http_client.aclose()
            if app.state.memory_publisher is not None:
                await app.state.memory_publisher.aclose()

    app = FastAPI(
        title="Eidolon Admin Gateway",
        version="0.1.0",
        lifespan=_lifespan,
    )
    app.state.registry = ServiceRegistry(cfg)
    app.state.gateway_config = cfg
    # trust_env=False: skip HTTP_PROXY/HTTPS_PROXY env vars. The gateway only
    # talks to localhost sub-projects, and a system proxy (e.g. Clash on :7890)
    # would otherwise intercept these requests and return 502 with multi-second
    # latency. NO_PROXY=127.0.0.1 cannot be relied on consistently across httpx
    # versions, so we just disable env-derived proxy config outright.
    app.state.http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, connect=5.0),
        trust_env=False,
    )
    app.state.supervisor_client = SupervisorClient(settings.supervisor_socket)
    app.state.supervisor_configs = ConfigStore(
        settings.supervisor_available_dir,
        settings.supervisor_enabled_dir,
    )
    # Lazy NATS publisher — connect on first use, not at startup, so admin
    # boots even when NATS is down. None is allowed for tests that don't
    # exercise memory write endpoints.
    app.state.memory_publisher = JetStreamPublisher()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.admin.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=False,
    )

    app.include_router(services_router, prefix="/api")
    app.include_router(overview_router, prefix="/api")
    app.include_router(supervisor_router, prefix="/api")
    app.include_router(memory_router, prefix="/api")
    app.include_router(channel_router, prefix="/api")
    app.include_router(client_web_router, prefix="/api")
    # NOTE: gateway router uses /api/services/{id}/{path:path}. It must be
    # registered AFTER /api/services so the catalog endpoint wins for the
    # exact path GET /api/services.
    app.include_router(gateway_router, prefix="/api")
    return app


app = create_app()
