"""Queue-backed commissioning channel for deterministic tests."""

from __future__ import annotations

import asyncio

from ...ports import CommissioningChannelClosed, CommissioningPacket


_STOP = object()


class InMemoryCommissioningChannel:
    """A test adapter, not a BLE or product transport implementation."""

    def __init__(self) -> None:
        self._running = False
        self._received: asyncio.Queue[CommissioningPacket | object] = asyncio.Queue()
        self._sent: asyncio.Queue[CommissioningPacket] = asyncio.Queue()

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        if self._running:
            return
        self._received = asyncio.Queue()
        self._sent = asyncio.Queue()
        self._running = True

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        await self._received.put(_STOP)

    async def receive(self) -> CommissioningPacket:
        self._require_running()
        packet = await self._received.get()
        if packet is _STOP:
            raise CommissioningChannelClosed("commissioning channel stopped")
        assert isinstance(packet, CommissioningPacket)
        return packet

    async def send(self, packet: CommissioningPacket) -> None:
        self._require_running()
        await self._sent.put(packet)

    async def inject_received(self, packet: CommissioningPacket) -> None:
        """Test-only helper that simulates a packet from a client."""
        self._require_running()
        await self._received.put(packet)

    async def next_sent(self) -> CommissioningPacket:
        """Test-only helper that observes a packet sent to a client."""
        self._require_running()
        return await self._sent.get()

    def _require_running(self) -> None:
        if not self._running:
            raise CommissioningChannelClosed("commissioning channel is not running")
