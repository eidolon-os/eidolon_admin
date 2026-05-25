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

import logging

from .channel.router import router as channel_router
from .client_web.router import router as client_web_router
from .configs.router import router as configs_router
from .devices import (
    DeviceBindingRepository,
    DeviceOrchestrator,
    router as devices_router,
)
from .gateway.registry import ServiceRegistry
from .gateway.router import router as gateway_router
from .memory.nats_publisher import JetStreamPublisher
from .memory.router import router as memory_router
from .nats_kv import KVClient
from .routers.overview import router as overview_router
from .routers.services import router as services_router
from .settings import GatewayConfig, Settings, get_settings, load_gateway_config
from .supervisor.client import SupervisorClient
from .supervisor.config import ConfigStore
from .supervisor.router import router as supervisor_router
from .system_health import router as system_health_router

logger = logging.getLogger(__name__)


def create_app(
    config: GatewayConfig | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    cfg = config or load_gateway_config(settings.services_file)

    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        # Devices module wiring: bring up NATS + ensure buckets + build the
        # orchestrator. NATS being down must NOT block admin-api startup —
        # the devices router checks ``app.state.device_orchestrator`` for None
        # and returns a clean 503, while every other admin feature keeps
        # running. This mirrors the same lazy / fault-tolerant philosophy
        # already used for memory_publisher.
        try:
            await app.state.nats_kv.connect()
            repo = DeviceBindingRepository(app.state.nats_kv)
            await repo.ensure_buckets()
            hub_url = _resolve_service_base_url(cfg, "hub")
            agent_url = _resolve_service_base_url(cfg, "agent")
            if hub_url and agent_url:
                app.state.device_orchestrator = DeviceOrchestrator(
                    repo=repo,
                    http_client=app.state.http_client,
                    hub_base_url=hub_url,
                    agent_base_url=agent_url,
                )
                logger.info("device orchestrator ready (hub=%s, agent=%s)", hub_url, agent_url)
            else:
                logger.warning(
                    "device orchestrator NOT initialized — hub or agent service "
                    "missing from services.yaml"
                )
        except Exception:  # noqa: BLE001
            # Any failure (NATS down, buckets fail to create, etc.) → leave
            # device_orchestrator unset. Router emits 503; the rest of admin
            # is unaffected.
            logger.exception("device orchestrator init failed; /api/devices will return 503")

        try:
            yield
        finally:
            await app.state.http_client.aclose()
            if app.state.memory_publisher is not None:
                await app.state.memory_publisher.aclose()
            if app.state.nats_kv is not None:
                await app.state.nats_kv.close()

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
    # NATS KV client for device-binding storage. ensure_bucket happens in
    # lifespan (NOT here) so test-time app creation doesn't touch NATS.
    app.state.nats_kv = KVClient()
    # Populated in lifespan iff NATS connects + buckets ensure successfully.
    # Router checks for None to emit 503.
    app.state.device_orchestrator = None

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
    app.include_router(configs_router, prefix="/api")
    app.include_router(devices_router, prefix="/api")
    app.include_router(system_health_router, prefix="/api")
    # NOTE: gateway router uses /api/services/{id}/{path:path}. It must be
    # registered AFTER /api/services so the catalog endpoint wins for the
    # exact path GET /api/services.
    app.include_router(gateway_router, prefix="/api")
    return app


def _resolve_service_base_url(cfg: GatewayConfig, service_id: str) -> str | None:
    """Return the configured ``base_url`` for a service id, or ``None`` if
    the service is absent or has no upstream (e.g. native-integration only).

    Used by the lifespan to wire the device orchestrator to hub / agent
    without hardcoding port numbers; ports are owned by services.yaml.
    """
    svc = cfg.find(service_id)
    if svc is None or not svc.base_url:
        return None
    return svc.base_url


app = create_app()
