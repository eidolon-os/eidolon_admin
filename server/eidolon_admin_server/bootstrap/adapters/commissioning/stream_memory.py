"""In-memory reliable links for TLS and application-protocol tests."""

from __future__ import annotations

import asyncio
import uuid

from ...ports import CommissioningLink, CommissioningLinkClosed


_CLOSED = object()


class InMemoryCommissioningLink:
    def __init__(self, link_id: str) -> None:
        self._link_id = link_id
        self._incoming: asyncio.Queue[bytes | object] = asyncio.Queue()
        self._peer: InMemoryCommissioningLink | None = None
        self._closed = False

    @property
    def link_id(self) -> str:
        return self._link_id

    def connect(self, peer: "InMemoryCommissioningLink") -> None:
        self._peer = peer

    async def receive(self) -> bytes:
        item = await self._incoming.get()
        if item is _CLOSED:
            raise CommissioningLinkClosed("commissioning link is closed")
        assert isinstance(item, bytes)
        return item

    async def send(self, data: bytes) -> None:
        if self._closed or self._peer is None or self._peer._closed:
            raise CommissioningLinkClosed("commissioning link is closed")
        if not data:
            return
        await self._peer._incoming.put(bytes(data))

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._incoming.put(_CLOSED)
        if self._peer is not None:
            await self._peer._incoming.put(_CLOSED)


def in_memory_commissioning_link_pair(
    link_id: str | None = None,
) -> tuple[CommissioningLink, CommissioningLink]:
    resolved = link_id or str(uuid.uuid4())
    first = InMemoryCommissioningLink(resolved)
    second = InMemoryCommissioningLink(resolved)
    first.connect(second)
    second.connect(first)
    return first, second
