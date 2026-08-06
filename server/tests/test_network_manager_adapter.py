from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest

from eidolon_admin_server.bootstrap.adapters.network import (
    NetworkManagerProvisioning,
)
from eidolon_admin_server.bootstrap.domain import NetworkState
from eidolon_admin_server.bootstrap.ports import (
    NetworkProvisioningError,
    NetworkProvisioningSnapshot,
)


class _Manager:
    async def call_checkpoint_rollback(self, path: str) -> None:
        raise AssertionError(f"unexpected checkpoint rollback: {path}")

    async def call_checkpoint_destroy(self, path: str) -> None:
        raise AssertionError(f"unexpected checkpoint destroy: {path}")


class _Properties:
    def __init__(
        self,
        *,
        startup_values: list[bool],
        on_startup_read: Callable[[bool], None] | None = None,
    ) -> None:
        self._startup_values = iter(startup_values)
        self._on_startup_read = on_startup_read

    async def call_get(self, interface: str, name: str) -> Any:
        if name == "Checkpoints":
            return SimpleNamespace(value=[])
        if name == "Startup":
            value = next(self._startup_values)
            if self._on_startup_read is not None:
                self._on_startup_read(value)
            return SimpleNamespace(value=value)
        raise AssertionError(f"unexpected property read: {interface}.{name}")


@pytest.mark.asyncio
async def test_recovery_waits_for_network_manager_startup_before_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    properties = _Properties(
        startup_values=[True, False],
        on_startup_read=lambda value: events.append(f"startup:{value}"),
    )
    adapter = NetworkManagerProvisioning(manager_startup_poll_seconds=0)
    snapshot = NetworkProvisioningSnapshot(
        state=NetworkState.CONNECTED,
        active_operation_id=None,
        current_ssid="Eidolon Wi-Fi",
        staged_ssid=None,
    )

    async def interfaces() -> tuple[Any, Any, str]:
        return _Manager(), object(), "/org/freedesktop/NetworkManager/Devices/3"

    async def get_properties(path: str) -> _Properties:
        assert path == "/org/freedesktop/NetworkManager"
        return properties

    async def get_state() -> NetworkProvisioningSnapshot:
        events.append("snapshot")
        return snapshot

    monkeypatch.setattr(adapter, "_interfaces", interfaces)
    monkeypatch.setattr(adapter, "_properties", get_properties)
    monkeypatch.setattr(adapter, "get_state", get_state)

    assert await adapter.recover_interrupted() == snapshot
    assert events == ["startup:True", "startup:False", "snapshot"]


@pytest.mark.asyncio
async def test_recovery_fails_closed_if_network_manager_never_settles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    properties = _Properties(startup_values=[True])
    adapter = NetworkManagerProvisioning(manager_startup_timeout_seconds=0)

    async def interfaces() -> tuple[Any, Any, str]:
        return _Manager(), object(), "/org/freedesktop/NetworkManager/Devices/3"

    async def get_properties(path: str) -> _Properties:
        assert path == "/org/freedesktop/NetworkManager"
        return properties

    monkeypatch.setattr(adapter, "_interfaces", interfaces)
    monkeypatch.setattr(adapter, "_properties", get_properties)

    with pytest.raises(
        NetworkProvisioningError,
        match="interrupted checkpoint recovery failed",
    ):
        await adapter.recover_interrupted()

    with pytest.raises(NetworkProvisioningError, match="recovery is incomplete"):
        await adapter.begin_change(
            SimpleNamespace(operation_id="op", ssid="wifi")  # type: ignore[arg-type]
        )
