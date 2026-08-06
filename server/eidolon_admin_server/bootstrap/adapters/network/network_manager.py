"""NetworkManager D-Bus implementation of the network provisioning port."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any

from dbus_next import BusType, Variant
from dbus_next.aio import MessageBus

from ...domain import NetworkState
from ...ports import (
    NetworkChangeRequest,
    NetworkProvisioningError,
    NetworkProvisioningSnapshot,
    WifiAccessPoint,
)


_NM_NAME = "org.freedesktop.NetworkManager"
_NM_PATH = "/org/freedesktop/NetworkManager"
_NM_IFACE = "org.freedesktop.NetworkManager"
_SETTINGS_PATH = "/org/freedesktop/NetworkManager/Settings"
_SETTINGS_IFACE = "org.freedesktop.NetworkManager.Settings"
_SETTINGS_CONNECTION_IFACE = "org.freedesktop.NetworkManager.Settings.Connection"
_DEVICE_IFACE = "org.freedesktop.NetworkManager.Device"
_WIFI_IFACE = "org.freedesktop.NetworkManager.Device.Wireless"
_AP_IFACE = "org.freedesktop.NetworkManager.AccessPoint"
_CHECKPOINT_IFACE = "org.freedesktop.NetworkManager.Checkpoint"
_PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"
_WIFI_DEVICE_TYPE = 2
_DEVICE_STATE_ACTIVATED = 100
_DEVICE_STATE_FAILED = 120


@dataclass(slots=True)
class _ActiveNetworkChange:
    operation_id: str
    checkpoint_path: str
    previous_ssid: str | None
    staged_ssid: str


class NetworkManagerProvisioning:
    """Use NetworkManager as the sole Wi-Fi profile and credential authority."""

    def __init__(
        self,
        *,
        scan_settle_seconds: float = 2.0,
        activation_timeout_seconds: float = 35.0,
        rollback_timeout_seconds: int = 90,
    ) -> None:
        self._scan_settle_seconds = scan_settle_seconds
        self._activation_timeout_seconds = activation_timeout_seconds
        self._rollback_timeout_seconds = rollback_timeout_seconds
        self._bus: MessageBus | None = None
        self._manager: Any = None
        self._device_path: str | None = None
        self._active: _ActiveNetworkChange | None = None
        self._recovery_complete = True

    async def recover_interrupted(self) -> NetworkProvisioningSnapshot:
        """Rollback checkpoints for this Wi-Fi device after bootstrapd restart."""

        self._recovery_complete = False
        manager, _, device_path = await self._interfaces()
        try:
            manager_properties = await self._properties(_NM_PATH)
            checkpoints = await manager_properties.call_get(_NM_IFACE, "Checkpoints")
            for checkpoint_path in checkpoints.value:
                properties = await self._properties(checkpoint_path)
                devices = await properties.call_get(_CHECKPOINT_IFACE, "Devices")
                if device_path not in devices.value:
                    continue
                await manager.call_checkpoint_rollback(checkpoint_path)
                await manager.call_checkpoint_destroy(checkpoint_path)
            self._active = None
            self._recovery_complete = True
            return await self.get_state()
        except Exception as exc:
            raise NetworkProvisioningError(
                "NetworkManager interrupted checkpoint recovery failed"
            ) from exc

    async def scan(self) -> list[WifiAccessPoint]:
        _, wifi, _ = await self._interfaces()
        try:
            await wifi.call_request_scan({})
        except Exception as exc:  # D-Bus errors are normalized at this port.
            raise NetworkProvisioningError("NetworkManager Wi-Fi scan failed") from exc
        if self._scan_settle_seconds:
            await asyncio.sleep(self._scan_settle_seconds)
        try:
            paths = await wifi.call_get_all_access_points()
            results = []
            for path in paths:
                properties = await self._properties(path)
                values = await properties.call_get_all(_AP_IFACE)
                ssid = bytes(values["Ssid"].value).decode("utf-8", errors="replace")
                if not ssid:
                    continue
                secured = any(
                    int(values.get(name, Variant("u", 0)).value) != 0
                    for name in ("Flags", "WpaFlags", "RsnFlags")
                )
                results.append(
                    WifiAccessPoint(
                        ssid=ssid,
                        signal=int(values["Strength"].value),
                        secured=secured,
                    )
                )
            return results
        except Exception as exc:
            raise NetworkProvisioningError(
                "NetworkManager access-point query failed"
            ) from exc

    async def get_state(self) -> NetworkProvisioningSnapshot:
        current_ssid = await self._current_ssid()
        active = self._active
        return NetworkProvisioningSnapshot(
            state=(
                NetworkState.STAGING
                if active is not None
                else NetworkState.CONNECTED
                if current_ssid is not None
                else NetworkState.UNCONFIGURED
            ),
            active_operation_id=None if active is None else active.operation_id,
            current_ssid=current_ssid,
            staged_ssid=None if active is None else active.staged_ssid,
        )

    async def begin_change(
        self, request: NetworkChangeRequest
    ) -> NetworkProvisioningSnapshot:
        if self._active is not None:
            raise NetworkProvisioningError("another network change is active")
        if not self._recovery_complete:
            raise NetworkProvisioningError("interrupted network recovery is incomplete")
        operation_id = request.operation_id.strip()
        ssid = request.ssid
        if not operation_id or not ssid:
            raise NetworkProvisioningError("operation_id and ssid are required")
        manager, _, device_path = await self._interfaces()
        previous_ssid = await self._current_ssid()
        checkpoint_path = "/"
        try:
            checkpoint_path = await manager.call_checkpoint_create(
                [device_path], self._rollback_timeout_seconds, 0
            )
            settings = self._connection_settings(request)
            await manager.call_add_and_activate_connection2(
                settings,
                device_path,
                "/",
                {"persist": Variant("s", "disk")},
            )
            self._active = _ActiveNetworkChange(
                operation_id=operation_id,
                checkpoint_path=checkpoint_path,
                previous_ssid=previous_ssid,
                staged_ssid=ssid,
            )
            await self._wait_until_activated(device_path)
        except Exception as exc:
            if checkpoint_path != "/":
                await self._best_effort_rollback(manager, checkpoint_path)
            self._active = None
            if isinstance(exc, NetworkProvisioningError):
                raise
            raise NetworkProvisioningError(
                "NetworkManager could not activate the staged network"
            ) from exc
        return await self.get_state()

    async def confirm(self, operation_id: str) -> NetworkProvisioningSnapshot:
        active = self._require_active(operation_id)
        manager, _, _ = await self._interfaces()
        if await self._current_ssid() != active.staged_ssid:
            raise NetworkProvisioningError("staged network is not active")
        try:
            await manager.call_checkpoint_destroy(active.checkpoint_path)
        except Exception as exc:
            raise NetworkProvisioningError(
                "NetworkManager checkpoint confirmation failed"
            ) from exc
        self._active = None
        return await self.get_state()

    async def rollback(self, operation_id: str) -> NetworkProvisioningSnapshot:
        active = self._require_active(operation_id)
        manager, _, _ = await self._interfaces()
        try:
            await manager.call_checkpoint_rollback(active.checkpoint_path)
            await manager.call_checkpoint_destroy(active.checkpoint_path)
        except Exception as exc:
            raise NetworkProvisioningError(
                "NetworkManager checkpoint rollback failed"
            ) from exc
        self._active = None
        return await self.get_state()

    async def forget_all_wifi_profiles(self) -> NetworkProvisioningSnapshot:
        """Delete every saved Wi-Fi profile to reproduce a factory-like network state."""

        if self._active is not None:
            await self.rollback(self._active.operation_id)
        bus = await self._connect()
        try:
            _, _, device_path = await self._interfaces()
            introspection = await bus.introspect(_NM_NAME, _SETTINGS_PATH)
            proxy = bus.get_proxy_object(_NM_NAME, _SETTINGS_PATH, introspection)
            settings = proxy.get_interface(_SETTINGS_IFACE)
            for path in await settings.call_list_connections():
                connection_introspection = await bus.introspect(_NM_NAME, path)
                connection_proxy = bus.get_proxy_object(
                    _NM_NAME,
                    path,
                    connection_introspection,
                )
                connection = connection_proxy.get_interface(
                    _SETTINGS_CONNECTION_IFACE
                )
                values = await connection.call_get_settings()
                connection_group = values.get("connection", {})
                connection_type = connection_group.get("type")
                if (
                    connection_type is not None
                    and connection_type.value == "802-11-wireless"
                ):
                    await connection.call_delete()
            if await self._current_ssid() is not None:
                device_introspection = await bus.introspect(_NM_NAME, device_path)
                device_proxy = bus.get_proxy_object(
                    _NM_NAME,
                    device_path,
                    device_introspection,
                )
                await device_proxy.get_interface(_DEVICE_IFACE).call_disconnect()
            loop = asyncio.get_running_loop()
            deadline = loop.time() + 5
            while await self._current_ssid() is not None:
                if loop.time() >= deadline:
                    raise NetworkProvisioningError(
                        "Wi-Fi remained connected after profile reset"
                    )
                await asyncio.sleep(0.1)
        except Exception as exc:
            if isinstance(exc, NetworkProvisioningError):
                raise
            raise NetworkProvisioningError(
                "NetworkManager could not forget saved Wi-Fi profiles"
            ) from exc
        self._active = None
        return NetworkProvisioningSnapshot(
            state=NetworkState.UNCONFIGURED,
            active_operation_id=None,
            current_ssid=None,
            staged_ssid=None,
        )

    def _require_active(self, operation_id: str) -> _ActiveNetworkChange:
        if self._active is None or self._active.operation_id != operation_id:
            raise NetworkProvisioningError("network operation does not match")
        return self._active

    async def _connect(self) -> MessageBus:
        if self._bus is None:
            try:
                self._bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
            except Exception as exc:
                raise NetworkProvisioningError(
                    "NetworkManager system bus is unavailable"
                ) from exc
        return self._bus

    async def _interfaces(self) -> tuple[Any, Any, str]:
        bus = await self._connect()
        if self._manager is None:
            introspection = await bus.introspect(_NM_NAME, _NM_PATH)
            proxy = bus.get_proxy_object(_NM_NAME, _NM_PATH, introspection)
            self._manager = proxy.get_interface(_NM_IFACE)
        if self._device_path is None:
            for path in await self._manager.call_get_devices():
                properties = await self._properties(path)
                device_type = await properties.call_get(_DEVICE_IFACE, "DeviceType")
                if int(device_type.value) == _WIFI_DEVICE_TYPE:
                    self._device_path = path
                    break
        if self._device_path is None:
            raise NetworkProvisioningError("NetworkManager has no Wi-Fi device")
        introspection = await bus.introspect(_NM_NAME, self._device_path)
        proxy = bus.get_proxy_object(_NM_NAME, self._device_path, introspection)
        return self._manager, proxy.get_interface(_WIFI_IFACE), self._device_path

    async def _properties(self, path: str) -> Any:
        bus = await self._connect()
        introspection = await bus.introspect(_NM_NAME, path)
        proxy = bus.get_proxy_object(_NM_NAME, path, introspection)
        return proxy.get_interface(_PROPERTIES_IFACE)

    async def _current_ssid(self) -> str | None:
        try:
            _, _, device_path = await self._interfaces()
            properties = await self._properties(device_path)
            active_ap = await properties.call_get(_WIFI_IFACE, "ActiveAccessPoint")
            if active_ap.value == "/":
                return None
            ap_properties = await self._properties(active_ap.value)
            ssid = await ap_properties.call_get(_AP_IFACE, "Ssid")
            return bytes(ssid.value).decode("utf-8", errors="replace") or None
        except NetworkProvisioningError:
            raise
        except Exception as exc:
            raise NetworkProvisioningError(
                "NetworkManager active network query failed"
            ) from exc

    async def _wait_until_activated(self, device_path: str) -> None:
        properties = await self._properties(device_path)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._activation_timeout_seconds
        while loop.time() < deadline:
            state = int((await properties.call_get(_DEVICE_IFACE, "State")).value)
            if state == _DEVICE_STATE_ACTIVATED:
                return
            if state >= _DEVICE_STATE_FAILED:
                raise NetworkProvisioningError("Wi-Fi activation failed")
            await asyncio.sleep(0.5)
        raise NetworkProvisioningError("Wi-Fi activation timed out")

    async def _best_effort_rollback(self, manager: Any, checkpoint_path: str) -> None:
        try:
            await manager.call_checkpoint_rollback(checkpoint_path)
        except Exception:
            return
        try:
            await manager.call_checkpoint_destroy(checkpoint_path)
        except Exception:
            return

    @staticmethod
    def _connection_settings(
        request: NetworkChangeRequest,
    ) -> dict[str, dict[str, Variant]]:
        settings: dict[str, dict[str, Variant]] = {
            "connection": {
                "id": Variant("s", f"Eidolon setup: {request.ssid}"),
                "type": Variant("s", "802-11-wireless"),
                "uuid": Variant("s", str(uuid.uuid4())),
                "autoconnect": Variant("b", True),
            },
            "802-11-wireless": {
                "ssid": Variant("ay", request.ssid.encode("utf-8")),
                "mode": Variant("s", "infrastructure"),
                "hidden": Variant("b", request.hidden),
            },
            "ipv4": {"method": Variant("s", "auto")},
            "ipv6": {"method": Variant("s", "auto")},
        }
        if request.passphrase is not None:
            settings["802-11-wireless-security"] = {
                "key-mgmt": Variant("s", "wpa-psk"),
                "psk": Variant("s", request.passphrase),
            }
        return settings
