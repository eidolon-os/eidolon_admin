"""Product-level network change port without NetworkManager concepts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from ..domain import NetworkState


class NetworkProvisioningError(RuntimeError):
    """A network change cannot proceed in the current adapter state."""


@dataclass(frozen=True, slots=True)
class NetworkChangeRequest:
    operation_id: str
    ssid: str
    passphrase: str | None = field(default=None, repr=False)
    hidden: bool = False


@dataclass(frozen=True, slots=True)
class WifiAccessPoint:
    ssid: str
    signal: int
    secured: bool


@dataclass(frozen=True, slots=True)
class NetworkProvisioningSnapshot:
    state: NetworkState
    active_operation_id: str | None
    current_ssid: str | None
    staged_ssid: str | None


@runtime_checkable
class NetworkProvisioning(Protocol):
    async def recover_interrupted(self) -> NetworkProvisioningSnapshot: ...

    async def scan(self) -> list[WifiAccessPoint]: ...

    async def get_state(self) -> NetworkProvisioningSnapshot: ...

    async def begin_change(
        self,
        request: NetworkChangeRequest,
    ) -> NetworkProvisioningSnapshot: ...

    async def confirm(self, operation_id: str) -> NetworkProvisioningSnapshot: ...

    async def rollback(self, operation_id: str) -> NetworkProvisioningSnapshot: ...
