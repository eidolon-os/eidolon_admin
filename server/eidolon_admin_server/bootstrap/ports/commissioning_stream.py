"""Reliable ordered byte-stream boundary implemented by the BLE adapter."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


class CommissioningLinkClosed(RuntimeError):
    """The nearby commissioning link disconnected or was closed."""


@runtime_checkable
class CommissioningLink(Protocol):
    @property
    def link_id(self) -> str: ...

    async def receive(self) -> bytes: ...

    async def send(self, data: bytes) -> None: ...

    async def close(self) -> None: ...


@runtime_checkable
class CommissioningListener(Protocol):
    @property
    def is_running(self) -> bool: ...

    async def start(self) -> None: ...

    async def accept(self) -> CommissioningLink: ...

    async def stop(self) -> None: ...
