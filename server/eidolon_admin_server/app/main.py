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

from .benchmarks import router as benchmarks_router
from .channel.router import router as channel_router
from .client_web.router import router as client_web_router
from .configs.router import router as configs_router
from .gateway.registry import ServiceRegistry
from .gateway.router import router as gateway_router
from .memory.nats_publisher import JetStreamPublisher
from .memory.router import router as memory_router
from .nats_kv import KVClient
from .registry.agents import (
    AgentMetadataRepository,
    AgentOrchestrator,
    AgentProjectClient,
    router as agents_router,
)
from .registry.devices import (
    DeviceBindingRepository,
    DeviceOrchestrator,
    HubDeviceClient,
    router as devices_router,
)
from .registry.resolve import ResolveOrchestrator, router as resolve_router
from .registry.templates import (
    TemplateAgentClient,
    TemplateOrchestrator,
    router as templates_router,
)
from .registry.templates.orchestrator import TemplateNotFound
from .registry.tenants import (
    TenantOrchestrator,
    router as tenants_router,
    seed_default as seed_default_tenant,
)
from .registry.users import (
    MemoryUserClient,
    UserOrchestrator,
    router as users_router,
)
from .registry.voiceprints import (
    VoiceprintStore,
    router as voiceprints_router,
)
from .routers.bootstrap import router as bootstrap_router
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

        tenant_repo = None
        user_repo = None
        registry_store = None
        ag_repo = None
        binding_repo = None
        hub_client = None

        # Control-plane registry data lives in the shared SQLite DB. Seed the
        # default tenant regardless of NATS state so user CRUD can still
        # validate tenant ownership when the bus is down.
        try:
            from eidolon_sdk.adapters.registry_sqlite import (
                AgentMetadataRepository as SqliteAgentMetadataRepository,
                DeviceBindingRepository as SqliteDeviceBindingRepository,
                RegistrySqliteStore,
                TenantRepository,
                UserRepository,
            )

            registry_store = RegistrySqliteStore(settings.registry_db_path)
            app.state.registry_store = registry_store
            tenant_repo = TenantRepository(registry_store)
            user_repo = UserRepository(registry_store)
            ag_repo = AgentMetadataRepository(
                SqliteAgentMetadataRepository(registry_store)
            )
            binding_repo = DeviceBindingRepository(
                SqliteDeviceBindingRepository(registry_store)
            )
            tenant_orch = TenantOrchestrator(tenant_repo)
            app.state.tenant_orchestrator = tenant_orch
            created = await seed_default_tenant(tenant_orch)
            if created:
                logger.info("registry: seeded default tenant on first start")
            else:
                logger.debug("registry: default tenant already present")
        except Exception:  # noqa: BLE001
            logger.exception(
                "tenant registry init failed; /api/tenants will return 503 "
                "until the local registry DB is available",
            )

        # Templates module — purely an HTTP proxy to agent. Doesn't need
        # NATS, only the agent service URL from services.yaml. If agent
        # is absent (services.yaml misconfigured), leave orchestrator
        # None and /api/templates 503s; same fault-tolerant pattern.
        agent_url_for_templates = _resolve_service_base_url(cfg, "agent")
        if agent_url_for_templates:
            client = TemplateAgentClient(
                app.state.http_client, agent_url_for_templates
            )
            app.state.template_orchestrator = TemplateOrchestrator(client)
            logger.info("template orchestrator ready (agent=%s)", agent_url_for_templates)
        else:
            logger.warning(
                "template orchestrator NOT initialized — agent service "
                "missing from services.yaml"
            )

        # Users module — admin's local registry DB is the user source of
        # truth; memory's supervisor admin HTTP provides health/reconcile.
        memory_admin_url = _resolve_memory_supervisor_url()
        if memory_admin_url and getattr(
            app.state, "tenant_orchestrator", None
        ) is not None and user_repo is not None:
            user_client = MemoryUserClient(app.state.http_client, memory_admin_url)
            app.state.memory_user_client = user_client
            user_orch = UserOrchestrator(
                memory_client=user_client,
                metadata_repo=user_repo,
                tenant_orchestrator=app.state.tenant_orchestrator,
            )
            app.state.user_orchestrator = user_orch
            # Close the cascade gap noted in 29.A doc §10: now that Users
            # exists, wire its refcount method into Tenants's delete
            # check so we can't orphan users into a deleted tenant.
            app.state.tenant_orchestrator.set_user_refcount_provider(
                user_orch.count_users_for_tenant
            )
            logger.info("user orchestrator ready (memory=%s)", memory_admin_url)
        else:
            logger.warning(
                "user orchestrator NOT initialized — memory supervisor "
                "url / tenant orchestrator missing"
            )

        # Agents module — bridges agent project (persona instances) and
        # admin's registry metadata (single agent_id → composite key resolver).
        # Needs both agent service URL AND a working UserOrchestrator
        # (because every create validates the owning user).
        agent_url_for_agents = _resolve_service_base_url(cfg, "agent")
        template_orch = app.state.template_orchestrator
        user_orch_ready = getattr(app.state, "user_orchestrator", None)
        if (
            agent_url_for_agents
            and template_orch is not None
            and user_orch_ready is not None
            and ag_repo is not None
        ):
            ag_client = AgentProjectClient(
                app.state.http_client, agent_url_for_agents
            )

            # Template-existence checker: an async callable that returns
            # True iff a template by this id exists in agent. We wrap
            # TemplateOrchestrator.get() so the agent orchestrator
            # doesn't import TemplateOrchestrator directly (keeps the
            # dependency direction clean and easier to fake in tests).
            async def _template_exists(template_id: str) -> bool:
                try:
                    await template_orch.get(template_id)
                    return True
                except TemplateNotFound:
                    return False
                except Exception:  # noqa: BLE001 - any other error treated as missing
                    logger.exception(
                        "agent_orch template-exists check failed for %s",
                        template_id,
                    )
                    return False

            app.state.agent_orchestrator = AgentOrchestrator(
                agent_client=ag_client,
                metadata_repo=ag_repo,
                user_orchestrator=user_orch_ready,
                template_exists_check=_template_exists,
            )

            # 29.K back-pointer: now that AgentOrchestrator exists, give
            # UserOrchestrator a way to fetch agent_ids for the UserView
            # envelope. Until 29.K this field was always [] (silent gap
            # — the user-edit dropdown rendered no choices). Lambda
            # extracts just the ids since UserView.agent_ids: list[str].
            user_orch_ready.set_agent_ids_provider(
                lambda uid: app.state.agent_orchestrator.list_agent_ids_for_user(uid)
            )
            user_orch_ready.set_agent_delete_provider(
                lambda aid: app.state.agent_orchestrator.delete_agent(aid)
            )

            logger.info(
                "agent orchestrator ready (agent=%s)", agent_url_for_agents
            )
        else:
            logger.warning(
                "agent orchestrator NOT initialized — agent url / registry / "
                "user orchestrator missing"
            )

        # Devices module — Phase 29.G replacement for the old device-
        # creates-agent flow. Hub HTTP for the device fact, admin's registry
        # for the device→agent binding pointer.
        hub_url = _resolve_service_base_url(cfg, "hub")
        ag_repo_for_devices = ag_repo
        if (
            hub_url
            and ag_repo_for_devices is not None
            and binding_repo is not None
        ):
            hub_client = HubDeviceClient(app.state.http_client, hub_url)
            device_orch = DeviceOrchestrator(
                hub_client=hub_client,
                binding_repo=binding_repo,
                agent_lookup=ag_repo_for_devices.get,
            )
            app.state.device_orchestrator = device_orch
            # Cascade hook: when an agent is deleted, unbind every device
            # pointing at it (mirrors Tenant→User cascade in 29.E.1 and
            # Agent→User.active_agent in 29.F).
            app.state.agent_orchestrator.set_device_cascade_hook(
                device_orch.unbind_all_referring_to
            )
            logger.info("device orchestrator ready (hub=%s)", hub_url)
        else:
            logger.warning(
                "device orchestrator NOT initialized — hub url / registry / "
                "agent orchestrator missing"
            )

        # Resolve aggregator — pure read-only join across all four
        # entity orchestrators. Built last because it depends on all
        # of them being ready.
        if (
            app.state.template_orchestrator is not None
            and app.state.user_orchestrator is not None
            and app.state.agent_orchestrator is not None
            and app.state.device_orchestrator is not None
        ):
            app.state.resolve_orchestrator = ResolveOrchestrator(
                binding_repo=binding_repo,
                hub_client=hub_client,
                agent_meta_repo=ag_repo,
                user_orchestrator=app.state.user_orchestrator,
                template_orchestrator=app.state.template_orchestrator,
                voiceprint_store=app.state.voiceprint_store,
            )
            logger.info("resolve orchestrator ready")
        else:
            logger.warning(
                "resolve orchestrator NOT initialized — depends on all of "
                "templates / users / agents / devices being ready"
            )

        try:
            yield
        finally:
            await app.state.http_client.aclose()
            if app.state.memory_publisher is not None:
                await app.state.memory_publisher.aclose()
            if app.state.nats_kv is not None:
                await app.state.nats_kv.close()
            if app.state.registry_store is not None:
                await app.state.registry_store.dispose()

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
    # NATS KV client for bus-backed features. Registry data uses SQLite.
    app.state.nats_kv = KVClient()
    app.state.registry_store = None
    # Each entity module (Tenants/Templates/Users/Agents/Devices) +
    # the Resolve aggregator gets its own orchestrator slot. Routers
    # check the slot before serving and emit 503 if absent.
    app.state.tenant_orchestrator = None
    app.state.template_orchestrator = None
    app.state.memory_user_client = None
    app.state.user_orchestrator = None
    app.state.agent_orchestrator = None
    app.state.device_orchestrator = None
    app.state.resolve_orchestrator = None
    app.state.voiceprint_store = VoiceprintStore(settings.voiceprint_root)
    app.state.voiceprint_model_dir = settings.speaker_model_dir

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
    app.include_router(memory_router, prefix="/api")
    app.include_router(channel_router, prefix="/api")
    app.include_router(client_web_router, prefix="/api")
    app.include_router(configs_router, prefix="/api")
    app.include_router(tenants_router, prefix="/api")
    app.include_router(templates_router, prefix="/api")
    app.include_router(users_router, prefix="/api")
    app.include_router(voiceprints_router, prefix="/api")
    app.include_router(agents_router, prefix="/api")
    app.include_router(devices_router, prefix="/api")
    app.include_router(resolve_router, prefix="/api")
    app.include_router(bootstrap_router, prefix="/api")
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


def _resolve_memory_supervisor_url() -> str | None:
    """Resolve the URL for memory's supervisor-embedded admin HTTP.

    Memory's supervisor process listens on a port NOT exposed via
    services.yaml (it's internal, not a service users hit). We look at
    the env vars ports.py sets:
        EIDOLON_MEMORY_SUPERVISOR_HTTP_HOST  (default 127.0.0.1)
        EIDOLON_MEMORY_SUPERVISOR_HTTP_PORT  (default 8019)

    Returns ``None`` only if the env vars are explicitly cleared — the
    defaults always produce a valid URL.
    """
    import os

    host = os.environ.get("EIDOLON_MEMORY_SUPERVISOR_HTTP_HOST", "127.0.0.1").strip()
    port = os.environ.get("EIDOLON_MEMORY_SUPERVISOR_HTTP_PORT", "8019").strip()
    if not host or not port:
        return None
    return f"http://{host}:{port}"


app = create_app()
