"""BlueZ GATT implementation of a single nearby reliable commissioning link."""

# dbus-next intentionally encodes D-Bus signatures as string annotations.
# ruff: noqa: F722, F821

import asyncio
import json
import uuid
from collections.abc import Callable
from contextlib import suppress
from typing import Any

from dbus_next import BusType, DBusError, Variant
from dbus_next.aio import MessageBus
from dbus_next.constants import PropertyAccess
from dbus_next.service import ServiceInterface, dbus_property, method

from ...ports import CommissioningLink, CommissioningLinkClosed


INFO_CHARACTERISTIC_UUID = "30af68fb-163b-581f-a94c-1488e8b3b4fd"
RX_CHARACTERISTIC_UUID = "518d55c5-5433-5312-9099-a0a03c90f003"
TX_CHARACTERISTIC_UUID = "c8a3ab33-7e3a-5827-adf0-f4358a0cfe38"

_BLUEZ_NAME = "org.bluez"
_OBJECT_MANAGER = "org.freedesktop.DBus.ObjectManager"
_GATT_MANAGER = "org.bluez.GattManager1"
_ADVERTISING_MANAGER = "org.bluez.LEAdvertisingManager1"
_ADAPTER_IFACE = "org.bluez.Adapter1"
_APP_PATH = "/live/eidolon/bootstrap/gatt"
_SERVICE_PATH = f"{_APP_PATH}/service0"
_INFO_PATH = f"{_SERVICE_PATH}/info"
_RX_PATH = f"{_SERVICE_PATH}/rx"
_TX_PATH = f"{_SERVICE_PATH}/tx"
_ADVERTISEMENT_PATH = "/live/eidolon/bootstrap/advertisement0"
_CLOSED = object()
_MAX_GATT_VALUE_BYTES = 512


class BlueZCommissioningError(RuntimeError):
    """BlueZ cannot provide the requested commissioning link."""


class _BlueZCommissioningLink:
    def __init__(self, characteristic: "_TxCharacteristic", device_path: str) -> None:
        self._link_id = str(uuid.uuid4())
        self.device_path = device_path
        self._tx = characteristic
        self._incoming: asyncio.Queue[bytes | object] = asyncio.Queue()
        self._closed = False
        self.mtu = 23

    @property
    def link_id(self) -> str:
        return self._link_id

    async def receive(self) -> bytes:
        item = await self._incoming.get()
        if item is _CLOSED:
            raise CommissioningLinkClosed("BLE central disconnected")
        assert isinstance(item, bytes)
        return item

    async def send(self, data: bytes) -> None:
        if self._closed:
            raise CommissioningLinkClosed("BLE commissioning link is closed")
        maximum = min(_MAX_GATT_VALUE_BYTES, max(20, self.mtu - 3))
        for offset in range(0, len(data), maximum):
            await self._tx.indicate(data[offset : offset + maximum])

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._incoming.put(_CLOSED)

    async def feed(self, data: bytes, mtu: int | None) -> None:
        if self._closed:
            raise CommissioningLinkClosed("BLE commissioning link is closed")
        if mtu is not None and mtu >= 23:
            self.mtu = mtu
        await self._incoming.put(data)


class _GattService(ServiceInterface):
    def __init__(self, service_uuid: str) -> None:
        super().__init__("org.bluez.GattService1")
        self.service_uuid = service_uuid

    @dbus_property(access=PropertyAccess.READ)
    def UUID(self) -> "s":
        return self.service_uuid

    @dbus_property(access=PropertyAccess.READ)
    def Primary(self) -> "b":
        return True


class _InfoCharacteristic(ServiceInterface):
    def __init__(self, endpoint_provider: Callable[[], dict[str, Any]]) -> None:
        super().__init__("org.bluez.GattCharacteristic1")
        self._endpoint_provider = endpoint_provider

    @dbus_property(access=PropertyAccess.READ)
    def UUID(self) -> "s":
        return INFO_CHARACTERISTIC_UUID

    @dbus_property(access=PropertyAccess.READ)
    def Service(self) -> "o":
        return _SERVICE_PATH

    @dbus_property(access=PropertyAccess.READ)
    def Flags(self) -> "as":
        return ["read"]

    @method()
    def ReadValue(self, options: "a{sv}") -> "ay":
        payload = json.dumps(
            self._endpoint_provider(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        offset = int(options.get("offset", Variant("q", 0)).value)
        if offset > len(payload):
            raise DBusError("org.bluez.Error.InvalidOffset", "invalid read offset")
        return payload[offset:]


class _TxCharacteristic(ServiceInterface):
    def __init__(self) -> None:
        super().__init__("org.bluez.GattCharacteristic1")
        self._notifying = False
        self._confirmation: asyncio.Future[None] | None = None
        self._listener: BlueZCommissioningListener | None = None

    @dbus_property(access=PropertyAccess.READ)
    def UUID(self) -> "s":
        return TX_CHARACTERISTIC_UUID

    @dbus_property(access=PropertyAccess.READ)
    def Service(self) -> "o":
        return _SERVICE_PATH

    @dbus_property(access=PropertyAccess.READ)
    def Flags(self) -> "as":
        return ["indicate"]

    @dbus_property(access=PropertyAccess.READ)
    def Notifying(self) -> "b":
        return self._notifying

    @dbus_property(access=PropertyAccess.READ)
    def Value(self) -> "ay":
        return b""

    @method()
    def StartNotify(self):
        if self._notifying:
            raise DBusError("org.bluez.Error.InProgress", "already notifying")
        self._notifying = True
        self.emit_properties_changed({"Notifying": True})

    @method()
    async def StopNotify(self):
        self._notifying = False
        self.emit_properties_changed({"Notifying": False})
        if self._listener is not None:
            await self._listener.central_disconnected()

    @method()
    def Confirm(self):
        confirmation = self._confirmation
        if confirmation is not None and not confirmation.done():
            confirmation.set_result(None)

    async def indicate(self, value: bytes) -> None:
        if not self._notifying:
            raise CommissioningLinkClosed("BLE central is not subscribed")
        loop = asyncio.get_running_loop()
        self._confirmation = loop.create_future()
        self.emit_properties_changed({"Value": bytes(value)})
        try:
            await asyncio.wait_for(self._confirmation, timeout=5)
        except TimeoutError as exc:
            raise CommissioningLinkClosed("BLE indication was not confirmed") from exc
        finally:
            self._confirmation = None


class _RxCharacteristic(ServiceInterface):
    def __init__(self, listener: "BlueZCommissioningListener") -> None:
        super().__init__("org.bluez.GattCharacteristic1")
        self._listener = listener

    @dbus_property(access=PropertyAccess.READ)
    def UUID(self) -> "s":
        return RX_CHARACTERISTIC_UUID

    @dbus_property(access=PropertyAccess.READ)
    def Service(self) -> "o":
        return _SERVICE_PATH

    @dbus_property(access=PropertyAccess.READ)
    def Flags(self) -> "as":
        return ["write"]

    @method()
    async def WriteValue(self, value: "ay", options: "a{sv}"):
        device = options.get("device")
        if device is None or not isinstance(device.value, str):
            raise DBusError("org.bluez.Error.NotPermitted", "device is required")
        mtu_value = options.get("mtu")
        mtu = None if mtu_value is None else int(mtu_value.value)
        try:
            await self._listener.central_data(device.value, bytes(value), mtu)
        except CommissioningLinkClosed as exc:
            raise DBusError("org.bluez.Error.NotPermitted", str(exc)) from exc


class _ApplicationObjectManager(ServiceInterface):
    def __init__(self, service_uuid: str) -> None:
        super().__init__(_OBJECT_MANAGER)
        self._service_uuid = service_uuid

    @method()
    def GetManagedObjects(self) -> "a{oa{sa{sv}}}":
        characteristic_base = {
            "Service": Variant("o", _SERVICE_PATH),
        }
        return {
            _SERVICE_PATH: {
                "org.bluez.GattService1": {
                    "UUID": Variant("s", self._service_uuid),
                    "Primary": Variant("b", True),
                }
            },
            _INFO_PATH: {
                "org.bluez.GattCharacteristic1": {
                    **characteristic_base,
                    "UUID": Variant("s", INFO_CHARACTERISTIC_UUID),
                    "Flags": Variant("as", ["read"]),
                }
            },
            _RX_PATH: {
                "org.bluez.GattCharacteristic1": {
                    **characteristic_base,
                    "UUID": Variant("s", RX_CHARACTERISTIC_UUID),
                    "Flags": Variant("as", ["write"]),
                }
            },
            _TX_PATH: {
                "org.bluez.GattCharacteristic1": {
                    **characteristic_base,
                    "UUID": Variant("s", TX_CHARACTERISTIC_UUID),
                    "Flags": Variant("as", ["indicate"]),
                    "Notifying": Variant("b", False),
                }
            },
        }


class _Advertisement(ServiceInterface):
    def __init__(self, service_uuid: str, local_name: str) -> None:
        super().__init__("org.bluez.LEAdvertisement1")
        self._service_uuid = service_uuid
        self._local_name = local_name

    @dbus_property(access=PropertyAccess.READ)
    def Type(self) -> "s":
        return "peripheral"

    @dbus_property(access=PropertyAccess.READ)
    def ServiceUUIDs(self) -> "as":
        return [self._service_uuid]

    @dbus_property(access=PropertyAccess.READ)
    def LocalName(self) -> "s":
        return self._local_name

    @method()
    def Release(self):
        return None


class BlueZCommissioningListener:
    """Advertise one GATT service and accept at most one central at a time."""

    def __init__(
        self,
        *,
        service_uuid: str,
        host_id: str,
        endpoint_provider: Callable[[], dict[str, Any]],
    ) -> None:
        self._service_uuid = service_uuid
        self._host_id = host_id
        self._endpoint_provider = endpoint_provider
        self._bus: MessageBus | None = None
        self._gatt_manager: Any = None
        self._advertising_manager: Any = None
        self._running = False
        self._accepted: asyncio.Queue[CommissioningLink | object] = asyncio.Queue()
        self._link: _BlueZCommissioningLink | None = None
        self._tx = _TxCharacteristic()
        self._tx._listener = self

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        if self._running:
            return
        try:
            bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
            self._bus = bus
            root_introspection = await bus.introspect(_BLUEZ_NAME, "/")
            root_proxy = bus.get_proxy_object(_BLUEZ_NAME, "/", root_introspection)
            objects = await root_proxy.get_interface(
                _OBJECT_MANAGER
            ).call_get_managed_objects()
            adapter_path = next(
                (
                    path
                    for path, interfaces in objects.items()
                    if _GATT_MANAGER in interfaces
                    and _ADVERTISING_MANAGER in interfaces
                    and _ADAPTER_IFACE in interfaces
                    and bool(interfaces[_ADAPTER_IFACE]["Powered"].value)
                ),
                None,
            )
            if adapter_path is None:
                raise BlueZCommissioningError(
                    "BlueZ has no powered GATT advertising adapter"
                )
            adapter_introspection = await bus.introspect(_BLUEZ_NAME, adapter_path)
            adapter_proxy = bus.get_proxy_object(
                _BLUEZ_NAME, adapter_path, adapter_introspection
            )
            self._gatt_manager = adapter_proxy.get_interface(_GATT_MANAGER)
            self._advertising_manager = adapter_proxy.get_interface(
                _ADVERTISING_MANAGER
            )
            advertisement = _Advertisement(
                self._service_uuid,
                f"Eidolon-{self._host_id[-6:]}",
            )
            bus.export(_APP_PATH, _ApplicationObjectManager(self._service_uuid))
            bus.export(_SERVICE_PATH, _GattService(self._service_uuid))
            bus.export(_INFO_PATH, _InfoCharacteristic(self._endpoint_provider))
            bus.export(_RX_PATH, _RxCharacteristic(self))
            bus.export(_TX_PATH, self._tx)
            bus.export(_ADVERTISEMENT_PATH, advertisement)
            await self._gatt_manager.call_register_application(_APP_PATH, {})
            await self._advertising_manager.call_register_advertisement(
                _ADVERTISEMENT_PATH, {}
            )
            self._running = True
        except BlueZCommissioningError:
            await self.stop()
            raise
        except Exception as exc:
            await self.stop()
            raise BlueZCommissioningError(
                "BlueZ GATT commissioning registration failed"
            ) from exc

    async def accept(self) -> CommissioningLink:
        if not self._running:
            raise BlueZCommissioningError("BlueZ commissioning listener is stopped")
        item = await self._accepted.get()
        if item is _CLOSED:
            raise BlueZCommissioningError("BlueZ commissioning listener is stopped")
        assert isinstance(item, CommissioningLink)
        return item

    async def stop(self) -> None:
        if not self._running and self._bus is None:
            return
        self._running = False
        if self._link is not None:
            await self._link.close()
            self._link = None
        await self._accepted.put(_CLOSED)
        if self._advertising_manager is not None:
            with suppress(Exception):
                await self._advertising_manager.call_unregister_advertisement(
                    _ADVERTISEMENT_PATH
                )
        if self._gatt_manager is not None:
            with suppress(Exception):
                await self._gatt_manager.call_unregister_application(_APP_PATH)
        if self._bus is not None:
            for path in (
                _ADVERTISEMENT_PATH,
                _TX_PATH,
                _RX_PATH,
                _INFO_PATH,
                _SERVICE_PATH,
                _APP_PATH,
            ):
                with suppress(Exception):
                    self._bus.unexport(path)
            self._bus.disconnect()
            self._bus = None

    async def central_data(
        self, device_path: str, value: bytes, mtu: int | None
    ) -> None:
        if not self._running or not self._tx.Notifying:
            raise CommissioningLinkClosed("BLE central must subscribe before writing")
        if self._link is None:
            self._link = _BlueZCommissioningLink(self._tx, device_path)
            await self._accepted.put(self._link)
        elif self._link.device_path != device_path:
            raise CommissioningLinkClosed("another BLE central is commissioning")
        await self._link.feed(value, mtu)

    async def central_disconnected(self) -> None:
        if self._link is not None:
            await self._link.close()
            self._link = None
