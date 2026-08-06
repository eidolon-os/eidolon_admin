"""Always-on bootstrap daemon entrypoint.

The process does not restart itself. systemd owns restart policy so there is
one supervisor and one observable failure boundary.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from contextlib import suppress

from .adapters.commissioning import BlueZCommissioningListener
from .adapters.network import InMemoryNetworkProvisioning, NetworkManagerProvisioning
from .adapters.persistence import SQLiteBootstrapStateStore
from .commissioning_protocol import CommissioningProtocolSession
from .commissioning_service import CommissioningService
from .config import (
    BootstrapSettings,
    CommissioningAdapter,
    NetworkAdapter,
    load_bootstrap_settings,
)
from .control import BootstrapControlServer
from .identity import HostIdentityManager
from .instance_lock import BootstrapInstanceLock
from .service import BootstrapService
from .systemd_notify import SystemdNotifier
from .tls_session import CommissioningTlsSession, run_commissioning_tls_session


logger = logging.getLogger("eidolon.bootstrap")


async def _wait_or_stop(stop_event: asyncio.Event, seconds: float) -> None:
    with suppress(TimeoutError):
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)


async def _run_bluez_commissioning(
    *,
    settings: BootstrapSettings,
    service: BootstrapService,
    commissioning: CommissioningService,
    stop_event: asyncio.Event,
) -> None:
    context = CommissioningTlsSession.server_context(
        str(settings.commissioning_tls_pem_path)
    )
    sessions: set[asyncio.Task[None]] = set()
    while not stop_event.is_set():
        service.set_commissioning_status("starting")
        listener = BlueZCommissioningListener(
            service_uuid=settings.ble_service_uuid,
            host_id=service.public_descriptor()["host_id"],
            endpoint_provider=service.commissioning_endpoint,
        )
        try:
            await listener.start()
            service.set_commissioning_status("ready")
            logger.info("BLE commissioning listener is ready")
            while not stop_event.is_set():
                link = await listener.accept()
                task = asyncio.create_task(
                    run_commissioning_tls_session(
                        link,
                        context,
                        CommissioningProtocolSession(commissioning),
                    ),
                    name=f"bootstrap-commissioning-{link.link_id}",
                )
                sessions.add(task)
                task.add_done_callback(sessions.discard)
        except Exception:
            if not stop_event.is_set():
                service.set_commissioning_status("degraded")
                logger.exception("BLE commissioning listener failed; retrying")
        finally:
            await listener.stop()
        if not stop_event.is_set():
            await _wait_or_stop(stop_event, 5)
    for task in sessions:
        task.cancel()
    if sessions:
        await asyncio.gather(*sessions, return_exceptions=True)
    service.set_commissioning_status("stopping")


async def run_daemon(
    settings: BootstrapSettings,
    *,
    stop_event: asyncio.Event | None = None,
) -> None:
    settings.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    settings.runtime_dir.mkdir(mode=0o750, parents=True, exist_ok=True)
    settings.state_dir.chmod(0o700)
    settings.runtime_dir.chmod(0o750)

    store = SQLiteBootstrapStateStore(settings.database_path)
    identity_manager = HostIdentityManager(settings.identity_key_path, settings.mode)
    network = (
        NetworkManagerProvisioning()
        if settings.network_adapter is NetworkAdapter.NETWORK_MANAGER
        else InMemoryNetworkProvisioning()
    )
    service = BootstrapService(
        settings=settings,
        store=store,
        identity_manager=identity_manager,
        network=network,
    )
    control = BootstrapControlServer(settings.control_socket, service)
    notifier = SystemdNotifier.from_environ()
    event = stop_event or asyncio.Event()
    watchdog_task: asyncio.Task[None] | None = None
    commissioning_task: asyncio.Task[None] | None = None
    instance_lock = BootstrapInstanceLock(settings.instance_lock_path)
    instance_lock.acquire()
    try:
        service.initialize()
        try:
            recovered_network = await network.recover_interrupted()
            logger.info(
                "network provisioning recovery complete state=%s",
                recovered_network.state.value,
            )
            service.reconcile_network_state(recovered_network.state)
        except Exception:
            logger.exception("network provisioning recovery failed closed")
        commissioning = CommissioningService(store=store, network=network)
        await control.start()
        logger.info(
            "bootstrapd ready host_id=%s mode=%s socket=%s",
            identity_manager.identity.host_id,
            settings.mode.value,
            settings.control_socket,
        )
        notifier.ready(
            f"Bootstrap ready for {identity_manager.identity.host_id} "
            f"({settings.mode.value})"
        )
        if notifier.watchdog_interval_seconds is not None:
            watchdog_task = asyncio.create_task(
                notifier.run_watchdog(event),
                name="bootstrap-systemd-watchdog",
            )
        if settings.commissioning_adapter is CommissioningAdapter.BLUEZ:
            commissioning_task = asyncio.create_task(
                _run_bluez_commissioning(
                    settings=settings,
                    service=service,
                    commissioning=commissioning,
                    stop_event=event,
                ),
                name="bootstrap-bluez-supervisor",
            )
        if stop_event is None:
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGTERM, signal.SIGINT):
                with suppress(NotImplementedError, RuntimeError):
                    loop.add_signal_handler(sig, event.set)
        await event.wait()
    finally:
        notifier.stopping()
        if watchdog_task is not None:
            watchdog_task.cancel()
            with suppress(asyncio.CancelledError):
                await watchdog_task
        if commissioning_task is not None:
            commissioning_task.cancel()
            with suppress(asyncio.CancelledError):
                await commissioning_task
        try:
            await control.close()
        finally:
            try:
                service.shutdown()
            finally:
                instance_lock.release()


def main() -> None:
    argparse.ArgumentParser(
        prog="eidolon-bootstrapd",
        description="Always-on Eidolon OS host bootstrap control plane",
    ).parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        asyncio.run(run_daemon(load_bootstrap_settings()))
    except KeyboardInterrupt:
        return
    except Exception:
        logger.exception("bootstrapd terminated unexpectedly")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
