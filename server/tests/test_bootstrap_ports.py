from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from eidolon_admin_server.bootstrap.adapters.commissioning import (
    InMemoryCommissioningChannel,
)
from eidolon_admin_server.bootstrap.adapters.network import (
    InMemoryNetworkProvisioning,
)
from eidolon_admin_server.bootstrap.adapters.persistence import (
    InMemoryBootstrapStateStore,
    SQLiteBootstrapStateStore,
)
from eidolon_admin_server.bootstrap.config import BootstrapMode, BootstrapSettings
from eidolon_admin_server.bootstrap.domain import NetworkState
from eidolon_admin_server.bootstrap.identity import HostIdentityManager
from eidolon_admin_server.bootstrap.ports import (
    BootstrapStateStore,
    CommissioningChannel,
    CommissioningChannelClosed,
    CommissioningPacket,
    NetworkChangeRequest,
    NetworkProvisioning,
    NetworkProvisioningError,
)
from eidolon_admin_server.bootstrap.service import BootstrapService


def _settings(tmp_path: Path) -> BootstrapSettings:
    return BootstrapSettings(
        mode=BootstrapMode.DEVELOPMENT,
        state_dir=tmp_path / "state",
        runtime_dir=tmp_path / "run",
        control_socket=tmp_path / "run" / "control.sock",
        ble_service_uuid="179e2e95-b1ee-5aa5-8dcf-7519b6c7ac52",
    )


def test_service_runs_against_state_store_port_without_sqlite(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = InMemoryBootstrapStateStore()
    assert isinstance(store, BootstrapStateStore)

    service = BootstrapService(
        settings=settings,
        store=store,
        identity_manager=HostIdentityManager(
            settings.identity_key_path,
            settings.mode,
        ),
    )
    service.initialize()
    try:
        descriptor = service.issue_development_descriptor(300)
        assert descriptor["host_id"].startswith("ehost-")
        assert store.latest_commissioning_session() is not None
        assert service.health()["state"]["claim_state"] == "unclaimed"
    finally:
        service.shutdown()


@pytest.mark.asyncio
async def test_in_memory_commissioning_channel_implements_packet_port() -> None:
    channel = InMemoryCommissioningChannel()
    assert isinstance(channel, CommissioningChannel)
    await channel.start()

    incoming = CommissioningPacket("session-1", b"request")
    await channel.inject_received(incoming)
    assert await channel.receive() == incoming

    outgoing = CommissioningPacket("session-1", b"response")
    await channel.send(outgoing)
    assert await channel.next_sent() == outgoing

    waiting = asyncio.create_task(channel.receive())
    await channel.stop()
    with pytest.raises(CommissioningChannelClosed):
        await waiting
    with pytest.raises(CommissioningChannelClosed):
        await channel.send(outgoing)


@pytest.mark.asyncio
async def test_in_memory_network_adapter_stages_confirms_and_rolls_back() -> None:
    network = InMemoryNetworkProvisioning(current_ssid="old-network")
    assert isinstance(network, NetworkProvisioning)

    request = NetworkChangeRequest(
        operation_id="network-op-1",
        ssid="new-network",
        passphrase="not-persisted-by-this-adapter",
    )
    assert "not-persisted" not in repr(request)

    staged = await network.begin_change(request)
    assert staged.state is NetworkState.STAGING
    assert staged.current_ssid == "old-network"
    assert staged.staged_ssid == "new-network"

    rolled_back = await network.rollback("network-op-1")
    assert rolled_back.state is NetworkState.CONNECTED
    assert rolled_back.current_ssid == "old-network"

    await network.begin_change(
        NetworkChangeRequest(operation_id="network-op-2", ssid="new-network")
    )
    confirmed = await network.confirm("network-op-2")
    assert confirmed.state is NetworkState.CONNECTED
    assert confirmed.current_ssid == "new-network"
    assert confirmed.active_operation_id is None


@pytest.mark.asyncio
async def test_network_adapter_rejects_overlapping_or_wrong_operation() -> None:
    network = InMemoryNetworkProvisioning()
    await network.begin_change(
        NetworkChangeRequest(operation_id="network-op-1", ssid="network")
    )

    with pytest.raises(NetworkProvisioningError, match="another"):
        await network.begin_change(
            NetworkChangeRequest(operation_id="network-op-2", ssid="other")
        )
    with pytest.raises(NetworkProvisioningError, match="does not match"):
        await network.confirm("network-op-2")


def test_sqlite_v2_keeps_authority_and_drops_v1_daemon_diagnostics(
    tmp_path: Path,
) -> None:
    path = tmp_path / "bootstrap.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE bootstrap_state (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
            reset_epoch INTEGER NOT NULL CHECK (reset_epoch >= 0),
            claim_state TEXT NOT NULL,
            network_state TEXT NOT NULL,
            workspace_state TEXT NOT NULL,
            recovery_state TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE daemon_runs (
            run_id TEXT PRIMARY KEY,
            pid INTEGER NOT NULL,
            started_at TEXT NOT NULL,
            stopped_at TEXT
        );
        CREATE TABLE commissioning_sessions (
            session_id TEXT PRIMARY KEY,
            secret_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            consumed_at TEXT,
            revoked_at TEXT
        );
        INSERT INTO bootstrap_state VALUES (
            1, 7, 'claimed', 'connected', 'ready', 'normal',
            '2026-08-05T00:00:00Z'
        );
        INSERT INTO daemon_runs VALUES (
            'old-run', 42, '2026-08-05T00:00:00Z', NULL
        );
        INSERT INTO commissioning_sessions VALUES (
            'session-1', 'hash-only', '2026-08-05T00:00:00Z',
            '2026-08-05T00:05:00Z', NULL, NULL
        );
        PRAGMA user_version = 1;
        """
    )
    connection.close()

    store = SQLiteBootstrapStateStore(path)
    store.open()
    try:
        store.initialize("2026-08-05T01:00:00Z")
        tables = {
            row[0]
            for row in store.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        version = store.connection.execute("PRAGMA user_version").fetchone()[0]

        assert version == 2
        assert "daemon_runs" not in tables
        assert store.get_state().reset_epoch == 7
        assert store.latest_commissioning_session().session_id == "session-1"
    finally:
        store.close()


def test_fresh_sqlite_contains_only_durable_authority_tables(tmp_path: Path) -> None:
    store = SQLiteBootstrapStateStore(tmp_path / "bootstrap.sqlite3")
    store.open()
    try:
        store.initialize("2026-08-05T00:00:00Z")
        tables = {
            row[0]
            for row in store.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert tables == {"bootstrap_state", "commissioning_sessions"}
    finally:
        store.close()
