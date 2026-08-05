"""Minimal sd_notify/watchdog support without importing systemd bindings."""

from __future__ import annotations

import asyncio
import logging
import os
import socket
from dataclasses import dataclass
from typing import Mapping


logger = logging.getLogger("eidolon.bootstrap.systemd")


@dataclass(frozen=True, slots=True)
class SystemdNotifier:
    address: str | None
    watchdog_interval_seconds: float | None

    @classmethod
    def from_environ(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> SystemdNotifier:
        env = os.environ if environ is None else environ
        address = env.get("NOTIFY_SOCKET") or None
        interval: float | None = None
        watchdog_pid = env.get("WATCHDOG_PID")
        if watchdog_pid in (None, "", str(os.getpid())):
            try:
                usec = int(env.get("WATCHDOG_USEC", "0"))
            except ValueError:
                usec = 0
            if usec > 0:
                interval = max(1.0, usec / 2_000_000)
        return cls(address=address, watchdog_interval_seconds=interval)

    def notify(self, message: str) -> bool:
        if self.address is None:
            return False
        address = self.address
        if address.startswith("@"):
            address = f"\0{address[1:]}"
        notifier = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            notifier.sendto(message.encode("utf-8"), address)
            return True
        except OSError as exc:
            logger.warning("sd_notify failed: %s", exc)
            return False
        finally:
            notifier.close()

    def ready(self, status: str) -> None:
        self.notify(f"READY=1\nSTATUS={status}")

    def stopping(self) -> None:
        self.notify("STOPPING=1\nSTATUS=Bootstrap control plane stopping")

    async def run_watchdog(self, stop_event: asyncio.Event) -> None:
        interval = self.watchdog_interval_seconds
        if interval is None:
            return
        while not stop_event.is_set():
            self.notify("WATCHDOG=1")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
            except TimeoutError:
                continue
