"""FastAPI app factory for the Eidolon admin gateway."""
# ruff: noqa: E402
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
from eidolon_data import DataStore, load_settings
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import logging

from .benchmarks import router as benchmarks_router
from .channel.router import router as channel_router
from .client_web.router import router as client_web_router
from .configs.router import router as configs_router
from .data.hub_client import HubDeviceRuntimeClient
from .data.owner_delete_finalizer import finalize_owner_delete_jobs
from .data.router import router as data_router
from .data.schema_guard import ensure_eidolon_data_schema
from .devices import router as devices_router
from .guard.router import router as guard_router
from .gateway.registry import ServiceRegistry
from .gateway.router import router as gateway_router
from .memory.router import router as memory_router
from .mission_control import router as mission_control_router
from .memory.nats_publisher import JetStreamPublisher
from .memory.supervisor_client import build_memory_supervisor_client
from .nats_kv import KVClient
from .onboarding import router as onboarding_router
from .resolve import router as resolve_router
from .resolve.orchestrator import ResolveOrchestrator
from .routers.overview import router as overview_router
from .routers.services import router as services_router
from .settings import GatewayConfig, Settings, get_settings, load_gateway_config
from .supervisor.client import SupervisorClient
from .supervisor.config import ConfigStore
from .supervisor.router import router as supervisor_router
from .system_health import router as system_health_router
from .tools.esp32 import Esp32ToolService, router as esp32_tools_router
from .tools.mobile import MobileToolService, router as mobile_tools_router

logger = logging.getLogger(__name__)


def create_app(
    config: GatewayConfig | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    cfg = config or load_gateway_config(settings.services_file)

    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        # Bring up NATS for the modules that still need the message bus.
        # Control-plane registry data lives in SQLite, so NATS being down
        # must not block tenants/users/agents/devices/resolve startup.
        #
        # ``max_attempts=5`` tolerates ``sv restart admin:admin-api`` catching
        # NATS itself in a restart window. ~15s worst case (0.5/1/2/4/8
        # backoff), after which registry routes can still start from SQLite.
        try:
            await app.state.nats_kv.connect(max_attempts=5)
        except ConnectionError as exc:
            logger.warning(
                "NATS unavailable (%s); bus-backed routes will return 503. "
                "Start the full stack with ./deploy/dev/run_all.sh start.",
                exc,
            )

        data_store = None
        # Owner/companion data lives in eidolon_data's sovereign DB. The older
        # tenant/user/agent registry adapters were intentionally removed from
        # eidolon_data during the owner/companion hard switch; admin must not
        # reconstruct them at startup.
        try:
            data_store = DataStore.open(load_settings())
            await data_store.init_schema()
            await ensure_eidolon_data_schema(data_store)
            app.state.data_store = data_store
            app.state.resolve_orchestrator = ResolveOrchestrator(
                data_store=data_store
            )
            try:
                cleanup = await finalize_owner_delete_jobs(
                    data_store,
                    app.state.memory_supervisor_client,
                )
                if cleanup.get("attempted"):
                    logger.info("owner delete cleanup resumed: %s", cleanup)
            except Exception:  # noqa: BLE001
                logger.exception("owner delete cleanup finalizer failed at startup")
            logger.info("eidolon_data owner store ready")
        except Exception:  # noqa: BLE001
            logger.exception(
                "eidolon_data init failed; /api/owners will return 503 "
                "until eidolon_data is available",
            )

        try:
            yield
        finally:
            await app.state.http_client.aclose()
            if app.state.memory_publisher is not None:
                await app.state.memory_publisher.aclose()
            if app.state.nats_kv is not None:
                await app.state.nats_kv.close()
            if app.state.data_store is not None:
                await app.state.data_store.close()

    app = FastAPI(
        title="Eidolon Admin Gateway",
        version="0.1.0",
        lifespan=_lifespan,
    )
    app.state.registry = ServiceRegistry(cfg)
    app.state.gateway_config = cfg
    # Proxy isolation is handled at supervisord level (HTTP_PROXY="" +
    # NO_PROXY=loopback in deploy/dev/supervisord.conf [supervisord]
    # environment=). httpx default trust_env=True is fine here — NO_PROXY
    # already shields us from the macOS system-proxy fallback, and external
    # HTTP (if any future feature adds it) will correctly honor whatever
    # proxy the operator configured at the program level.
    app.state.http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, connect=5.0),
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
    app.state.memory_supervisor_client = build_memory_supervisor_client(app.state.http_client)
    hub_service = app.state.registry.get("hub")
    app.state.hub_device_client = HubDeviceRuntimeClient(
        app.state.http_client,
        hub_service.base_url if hub_service is not None else "",
    )
    # NATS KV client for bus-backed features. Registry data uses SQLite.
    app.state.nats_kv = KVClient()
    app.state.data_store = None
    app.state.resolve_orchestrator = None
    # Legacy tenant/user/agent registry orchestrators are intentionally not
    # initialized in the owner/companion model.
    app.state.voiceprint_model_dir = settings.speaker_model_dir
    app.state.esp32_tools = Esp32ToolService(catalog_file=settings.esp32_tools_file)
    app.state.mobile_tools = MobileToolService()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.admin.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=False,
    )

    app.include_router(services_router, prefix="/api")
    app.include_router(benchmarks_router, prefix="/api")
    app.include_router(overview_router, prefix="/api")
    app.include_router(supervisor_router, prefix="/api")
    app.include_router(channel_router, prefix="/api")
    app.include_router(client_web_router, prefix="/api")
    app.include_router(configs_router, prefix="/api")
    app.include_router(onboarding_router, prefix="/api")
    app.include_router(data_router, prefix="/api")
    app.include_router(guard_router, prefix="/api")
    app.include_router(devices_router, prefix="/api")
    app.include_router(resolve_router, prefix="/api")
    app.include_router(memory_router, prefix="/api")
    app.include_router(mission_control_router, prefix="/api")
    app.include_router(system_health_router, prefix="/api")
    app.include_router(esp32_tools_router, prefix="/api")
    app.include_router(mobile_tools_router, prefix="/api")
    # NOTE: gateway router uses /api/services/{id}/{path:path}. It must be
    # registered AFTER /api/services so the catalog endpoint wins for the
    # exact path GET /api/services.
    app.include_router(gateway_router, prefix="/api")
    return app

app = create_app()
