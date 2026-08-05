"""Transport-neutral commissioning message channel.

The port intentionally knows nothing about BlueZ, GATT characteristics, MTU,
notifications, LAN sockets, or a final security handshake.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class CommissioningChannelClosed(RuntimeError):
    """Raised when a packet operation is attempted on a stopped channel."""


@dataclass(frozen=True, slots=True)
class CommissioningPacket:
    session_id: str
    payload: bytes


@runtime_checkable
class CommissioningChannel(Protocol):
    @property
    def is_running(self) -> bool: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def receive(self) -> CommissioningPacket: ...

    async def send(self, packet: CommissioningPacket) -> None: ...
