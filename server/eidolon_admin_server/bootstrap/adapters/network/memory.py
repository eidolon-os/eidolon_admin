"""In-memory network adapter for state-machine tests."""

from __future__ import annotations

from ...domain import NetworkState
from ...ports import (
    NetworkChangeRequest,
    NetworkProvisioningError,
    NetworkProvisioningSnapshot,
    WifiAccessPoint,
)


class InMemoryNetworkProvisioning:
    """Simulates stage/confirm/rollback without claiming hardware behavior."""

    def __init__(
        self,
        *,
        current_ssid: str | None = None,
        access_points: list[WifiAccessPoint] | None = None,
    ) -> None:
        self._current_ssid = current_ssid
        self._previous_ssid: str | None = None
        self._staged_ssid: str | None = None
        self._active_operation_id: str | None = None
        self._state = (
            NetworkState.CONNECTED
            if current_ssid is not None
            else NetworkState.UNCONFIGURED
        )
        self._access_points = list(access_points or [])

    async def recover_interrupted(self) -> NetworkProvisioningSnapshot:
        if self._active_operation_id is not None:
            self._current_ssid = self._previous_ssid
            self._clear_operation()
            self._state = (
                NetworkState.CONNECTED
                if self._current_ssid is not None
                else NetworkState.UNCONFIGURED
            )
        return self._snapshot()

    async def scan(self) -> list[WifiAccessPoint]:
        return list(self._access_points)

    async def get_state(self) -> NetworkProvisioningSnapshot:
        return self._snapshot()

    async def begin_change(
        self,
        request: NetworkChangeRequest,
    ) -> NetworkProvisioningSnapshot:
        operation_id = request.operation_id.strip()
        ssid = request.ssid.strip()
        if not operation_id:
            raise NetworkProvisioningError("operation_id is required")
        if not ssid:
            raise NetworkProvisioningError("ssid is required")
        if self._active_operation_id is not None:
            raise NetworkProvisioningError("another network change is active")
        self._previous_ssid = self._current_ssid
        self._staged_ssid = ssid
        self._active_operation_id = operation_id
        self._state = NetworkState.STAGING
        return self._snapshot()

    async def confirm(self, operation_id: str) -> NetworkProvisioningSnapshot:
        self._require_operation(operation_id)
        self._current_ssid = self._staged_ssid
        self._clear_operation()
        self._state = NetworkState.CONNECTED
        return self._snapshot()

    async def rollback(self, operation_id: str) -> NetworkProvisioningSnapshot:
        self._require_operation(operation_id)
        self._current_ssid = self._previous_ssid
        self._clear_operation()
        self._state = (
            NetworkState.CONNECTED
            if self._current_ssid is not None
            else NetworkState.UNCONFIGURED
        )
        return self._snapshot()

    async def forget_all_wifi_profiles(self) -> NetworkProvisioningSnapshot:
        self._current_ssid = None
        self._clear_operation()
        self._state = NetworkState.UNCONFIGURED
        return self._snapshot()

    def _require_operation(self, operation_id: str) -> None:
        if not operation_id or operation_id != self._active_operation_id:
            raise NetworkProvisioningError("network operation does not match")

    def _clear_operation(self) -> None:
        self._active_operation_id = None
        self._staged_ssid = None
        self._previous_ssid = None

    def _snapshot(self) -> NetworkProvisioningSnapshot:
        return NetworkProvisioningSnapshot(
            state=self._state,
            active_operation_id=self._active_operation_id,
            current_ssid=self._current_ssid,
            staged_ssid=self._staged_ssid,
        )
