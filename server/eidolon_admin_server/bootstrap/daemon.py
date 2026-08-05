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

from .adapters.persistence import SQLiteBootstrapStateStore
from .config import BootstrapSettings, load_bootstrap_settings
from .control import BootstrapControlServer
from .identity import HostIdentityManager
from .instance_lock import BootstrapInstanceLock
from .service import BootstrapService
from .systemd_notify import SystemdNotifier


logger = logging.getLogger("eidolon.bootstrap")


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
    service = BootstrapService(
        settings=settings,
        store=store,
        identity_manager=identity_manager,
    )
    control = BootstrapControlServer(settings.control_socket, service)
    notifier = SystemdNotifier.from_environ()
    event = stop_event or asyncio.Event()
    watchdog_task: asyncio.Task[None] | None = None
    instance_lock = BootstrapInstanceLock(settings.instance_lock_path)
    instance_lock.acquire()
    try:
        service.initialize()
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
