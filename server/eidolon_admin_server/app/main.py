"""FastAPI composition root for the Eidolon Admin control plane."""

from __future__ import annotations

import asyncio
import pwd
from pathlib import Path
from contextlib import asynccontextmanager, suppress

import logging

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .benchmarks import router as benchmarks_router
from .channel.router import router as channel_router
from .client_web.router import router as client_web_router
from .configs.router import router as configs_router
from .control_plane.failure_handler import install_authority_failure_handler
from .control_plane.router import (
    operator_router as control_plane_operator_router,
    router as control_plane_router,
)
from .management.mission_control_router import router as management_mission_control_router
from .management.router import router as management_router
from .control_plane.service import ControlPlaneService
from .mission_control.router import router as mission_control_router
from .host_services.client import HostServiceClient
from .host_services.router import router as host_services_router
from .gateway.registry import ServiceRegistry
from .gateway.router import router as gateway_router
from .routers.overview import router as overview_router
from .routers.services import router as services_router
from ..audit import (
    AuditIndexSettings,
    AuditIndexStore,
    default_audit_index_path,
    run_audit_indexer,
)
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
from ..systemd_notify import SystemdNotifier


logger = logging.getLogger(__name__)


def _report_indexer_exit(task: asyncio.Task) -> None:
    """Say when the audit indexer stops, and why.

    ``asyncio`` only surfaces an unretrieved task exception at garbage
    collection, which in a long-lived process can be never. The indexer failing
    is exactly the kind of thing an operator needs told: no index means the
    Owner's events lane is dark and every authority's dispatcher is publishing
    into a stream nobody created.
    """

    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        logger.error("audit indexer stopped: %s: %s", type(error).__name__, error)


class LazyAuditIndex:
    """A read handle on the audit index, opened the first time it is read.

    Not at startup. The indexer that creates the file runs in this same
    process, so "does the file exist" is only true *after* a moment that has
    not happened yet when the lifespan runs — and a decision taken then is
    never revisited. Callers get the same ``tail_for_owner`` either way; when
    there is no index they get an error naming that, which is what the lane
    reports.
    """

    def __init__(self, sqlite_path: str) -> None:
        self._path = Path(sqlite_path)
        self._store: AuditIndexStore | None = None

    async def tail_for_owner(self, owner_id: str, **kwargs):
        if self._store is None:
            if not self._path.exists():
                raise FileNotFoundError(
                    f"这台 Host 还没有审计索引（{self._path}）：事件流没有在跑"
                )
            self._store = AuditIndexStore.open(
                AuditIndexSettings(sqlite_path=str(self._path), read_only=True)
            )
        return await self._store.tail_for_owner(owner_id, **kwargs)

    async def close(self) -> None:
        if self._store is not None:
            await self._store.close()
            self._store = None


def create_app(
    config: GatewayConfig | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    cfg = config or load_gateway_config(settings.services_file)
    systemd = SystemdNotifier.from_environ()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        broker = None
        indexer: asyncio.Task | None = None
        audit_reader = LazyAuditIndex(default_audit_index_path())
        try:
            # The Owner's map reads its events lane from here — the index
            # assigns their order, and that order is what makes the lane
            # resumable.
            #
            # Opened on first use, not now. Whether the index exists is a
            # runtime fact: the loop that creates it starts a few lines below,
            # in this same lifespan, so a startup existence check loses that
            # race and then never looks again. Absence stays a real state — a
            # Host with no indexer has no file, and the lane says it could not
            # be read rather than showing an Owner a house where nothing has
            # ever happened.
            app.state.audit_index = audit_reader
            # The audit index is this process's own projection, so the loop that
            # fills it lives here rather than in a unit of its own. Unset URL
            # means no indexer — and since the consumer is what creates the
            # stream, no authority publishes either. Nothing is lost by that:
            # they keep what they could not send.
            if settings.audit_nats_url:
                indexer = asyncio.create_task(
                    run_audit_indexer(
                        nats_url=settings.audit_nats_url,
                        sqlite_path=default_audit_index_path(),
                    ),
                    name="eidolon-admin-audit-indexer",
                )
                # A task nobody awaits is a failure with nowhere to be reported.
                # This one died on its first mkdir for months — the index path
                # used to sit outside the only directory this hardened unit may
                # write — and the process said nothing at all.
                indexer.add_done_callback(_report_indexer_exit)
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
            status = (
                "Admin removal capability broker ready"
                if broker is not None
                else "Admin control plane ready"
            )
            systemd.ready(status)
            yield
        finally:
            systemd.stopping("Admin control plane stopping")
            if indexer is not None:
                indexer.cancel()
                with suppress(asyncio.CancelledError):
                    await indexer
            if broker is not None:
                await broker.close()
            await audit_reader.close()
            await app.state.control_plane.close()
            await app.state.host_services.close()
            await app.state.http_client.aclose()

    app = FastAPI(
        title="Eidolon Admin Control Plane",
        version="0.1.0",
        lifespan=lifespan,
    )
    # Before any router is mounted, so no route can be added on a version of
    # this app where an upstream refusal is an unexplained 500.
    install_authority_failure_handler(app)
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
    app.include_router(control_plane_operator_router, prefix="/api")
    app.include_router(control_plane_router, prefix="/api")
    # Its own prefix, not another branch of control-plane: that family already
    # answers to two audiences and a third meaning is how this got confusing.
    app.include_router(management_router, prefix="/api")
    app.include_router(management_mission_control_router, prefix="/api")
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
