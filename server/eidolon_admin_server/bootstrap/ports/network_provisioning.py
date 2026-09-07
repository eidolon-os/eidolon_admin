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
    #: Whether this adapter's snapshots are observations of the Host's real
    #: network, or a simulation of one.
    #:
    #: Bootstrap keeps ``network_state`` durably, and a durable record must not
    #: be written from a report that is not an observation. The simulated
    #: adapter has no memory across processes, so a fresh one reports
    #: ``unconfigured`` — which means "I have never been told", not "this Host
    #: has no network". Reconciled at startup, that ignorance replaced a
    #: ``connected`` a claim had actually established, and the phone talking to
    #: the Host over that very network was shown a warning saying it had none.
    #:
    #: The concrete adapter has the same failure mode from a different cause —
    #: NetworkManager reports no active access point until its autoconnect
    #: policy settles — and handles it where it belongs, by waiting until its
    #: own answer is worth having. An adapter that can never make its answer
    #: worth having says so here instead.
    observes_host_network: bool

    async def recover_interrupted(self) -> NetworkProvisioningSnapshot: ...

    async def scan(self) -> list[WifiAccessPoint]: ...

    async def get_state(self) -> NetworkProvisioningSnapshot: ...

    async def begin_change(
        self,
        request: NetworkChangeRequest,
    ) -> NetworkProvisioningSnapshot: ...

    async def confirm(self, operation_id: str) -> NetworkProvisioningSnapshot: ...

    async def rollback(self, operation_id: str) -> NetworkProvisioningSnapshot: ...

    async def forget_all_wifi_profiles(self) -> NetworkProvisioningSnapshot: ...
