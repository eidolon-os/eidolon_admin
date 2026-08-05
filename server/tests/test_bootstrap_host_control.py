from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from eidolon_admin_server.bootstrap.adapters.persistence import (
    SQLiteBootstrapStateStore,
)
from eidolon_admin_server.bootstrap.config import (
    BootstrapConfigurationError,
    BootstrapMode,
    BootstrapSettings,
    load_bootstrap_settings,
)
from eidolon_admin_server.bootstrap.control import (
    BootstrapControlClient,
    BootstrapControlError,
    BootstrapControlServer,
)
from eidolon_admin_server.bootstrap.daemon import run_daemon
from eidolon_admin_server.bootstrap.identity import (
    HostIdentityError,
    HostIdentityManager,
    HostIdentityProvisioningRequired,
)
from eidolon_admin_server.bootstrap.instance_lock import (
    BootstrapAlreadyRunning,
    BootstrapInstanceLock,
)
from eidolon_admin_server.bootstrap.service import (
    BootstrapOperationRejected,
    BootstrapService,
)
from eidolon_admin_server.bootstrap.systemd_notify import SystemdNotifier
from eidolon_admin_server.local_api.app import create_app
from eidolon_admin_server.local_api.config import LocalApiSettings


def _settings(
    tmp_path: Path,
    mode: BootstrapMode = BootstrapMode.DEVELOPMENT,
    *,
    runtime_dir: Path | None = None,
):
    state_dir = tmp_path / "state"
    resolved_runtime_dir = runtime_dir or tmp_path / "run"
    return BootstrapSettings(
        mode=mode,
        state_dir=state_dir,
        runtime_dir=resolved_runtime_dir,
        control_socket=resolved_runtime_dir / "control.sock",
        ble_service_uuid="179e2e95-b1ee-5aa5-8dcf-7519b6c7ac52",
        dev_descriptor_ttl_seconds=1800,
    )


@pytest.fixture
def short_runtime_dir() -> Path:
    project_runtime = Path(__file__).resolve().parents[2] / "var"
    project_runtime.mkdir(exist_ok=True)
    path = Path(tempfile.mkdtemp(prefix="eb-", dir=project_runtime))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _service(settings: BootstrapSettings) -> BootstrapService:
    return BootstrapService(
        settings=settings,
        store=SQLiteBootstrapStateStore(settings.database_path),
        identity_manager=HostIdentityManager(
            settings.identity_key_path,
            settings.mode,
        ),
    )


def test_bootstrap_settings_default_to_fail_closed_production() -> None:
    settings = load_bootstrap_settings({})

    assert settings.mode is BootstrapMode.PRODUCTION


@pytest.mark.parametrize("mode", ["", "debug", "prod"])
def test_bootstrap_settings_reject_unknown_modes(mode: str) -> None:
    with pytest.raises(BootstrapConfigurationError):
        load_bootstrap_settings({"EIDOLON_BOOTSTRAP_MODE": mode})


def test_bootstrap_settings_reject_overlong_control_socket(tmp_path: Path) -> None:
    with pytest.raises(BootstrapConfigurationError, match="at most 100"):
        load_bootstrap_settings(
            {
                "EIDOLON_BOOTSTRAP_MODE": "development",
                "EIDOLON_BOOTSTRAP_STATE_DIR": str(tmp_path),
                "EIDOLON_BOOTSTRAP_RUNTIME_DIR": str(tmp_path),
                "EIDOLON_BOOTSTRAP_CONTROL_SOCKET": str(
                    tmp_path / ("socket-" + "x" * 120)
                ),
            }
        )


def test_development_identity_is_unique_stable_and_private(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    manager = HostIdentityManager(settings.identity_key_path, settings.mode)

    first = manager.load()
    second = HostIdentityManager(settings.identity_key_path, settings.mode).load()

    assert first == second
    assert first.host_id.startswith("ehost-")
    assert settings.identity_key_path.stat().st_mode & 0o777 == 0o600
    assert len(settings.identity_key_path.read_bytes()) == 32


def test_production_fails_without_manufacturing_identity(tmp_path: Path) -> None:
    settings = _settings(tmp_path, BootstrapMode.PRODUCTION)

    with pytest.raises(HostIdentityProvisioningRequired):
        HostIdentityManager(settings.identity_key_path, settings.mode).load()


def test_host_identity_rejects_symlink_even_in_development(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    target = tmp_path / "outside.key"
    target.write_bytes(b"x" * 32)
    target.chmod(0o600)
    settings.state_dir.mkdir(parents=True)
    settings.identity_key_path.symlink_to(target)

    with pytest.raises(HostIdentityError, match="symbolic link"):
        HostIdentityManager(settings.identity_key_path, settings.mode).load()


def test_bootstrap_instance_lock_prevents_two_host_authorities(
    short_runtime_dir: Path,
) -> None:
    lock_path = short_runtime_dir / "bootstrapd.lock"
    first = BootstrapInstanceLock(lock_path)
    second = BootstrapInstanceLock(lock_path)
    first.acquire()
    try:
        with pytest.raises(BootstrapAlreadyRunning):
            second.acquire()
    finally:
        first.release()
    second.acquire()
    second.release()


def test_development_descriptor_is_signed_and_secret_is_not_persisted(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    service = _service(settings)
    service.initialize()
    try:
        descriptor = service.issue_development_descriptor(300)
        assert settings.database_path.stat().st_mode & 0o777 == 0o600
        assert descriptor["mode"] == "development"
        assert descriptor["host_id"].startswith("ehost-")
        assert len(descriptor["commissioning_secret"]) >= 32
        assert len(descriptor["signature"]) >= 80

        unsigned = {key: value for key, value in descriptor.items() if key != "signature"}
        canonical = json.dumps(
            unsigned,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        public_key = base64.urlsafe_b64decode(
            descriptor["host_public_key"] + "="
        )
        signature = base64.urlsafe_b64decode(descriptor["signature"] + "==")
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, canonical)

        database_dump = "\n".join(
            service._store.connection.iterdump()  # noqa: SLF001 - persistence invariant
        )
        assert descriptor["commissioning_secret"] not in database_dump

        replacement = service.issue_development_descriptor(300)
        assert replacement["commissioning_id"] != descriptor["commissioning_id"]
        status = service.development_descriptor_status()["current"]
        assert status["session_id"] == replacement["commissioning_id"]
        assert "commissioning_secret" not in status
    finally:
        service.shutdown()


def test_production_rejects_development_descriptor_issuance(tmp_path: Path) -> None:
    development = _settings(tmp_path)
    HostIdentityManager(
        development.identity_key_path,
        development.mode,
    ).load()
    production = _settings(tmp_path, BootstrapMode.PRODUCTION)
    service = _service(production)
    service.initialize()
    try:
        with pytest.raises(BootstrapOperationRejected):
            service.issue_development_descriptor()
    finally:
        service.shutdown()


def test_store_rejects_unknown_schema_version(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.state_dir.mkdir(parents=True)
    connection = sqlite3.connect(settings.database_path)
    connection.execute("PRAGMA user_version = 99")
    connection.close()
    store = SQLiteBootstrapStateStore(settings.database_path)
    store.open()
    try:
        with pytest.raises(RuntimeError, match="unsupported bootstrap schema"):
            store.initialize("2026-08-05T00:00:00Z")
    finally:
        store.close()


@pytest.mark.asyncio
async def test_control_socket_exposes_read_state_and_dev_issuance(
    tmp_path: Path,
    short_runtime_dir: Path,
) -> None:
    settings = _settings(tmp_path, runtime_dir=short_runtime_dir)
    service = _service(settings)
    service.initialize()
    server = BootstrapControlServer(settings.control_socket, service)
    await server.start()
    client = BootstrapControlClient(settings.control_socket)
    try:
        health = await client.request("health")
        assert health["status"] == "running"
        assert health["state"]["claim_state"] == "unclaimed"

        descriptor = await client.request("dev.issue", ttl_seconds=300)
        assert descriptor["mode"] == "development"

        with pytest.raises(BootstrapControlError, match="unknown control operation"):
            await client.request("not.allowed")
    finally:
        await server.close()
        service.shutdown()


@pytest.mark.asyncio
async def test_daemon_remains_alive_until_supervisor_requests_stop(
    tmp_path: Path,
    short_runtime_dir: Path,
) -> None:
    settings = _settings(tmp_path, runtime_dir=short_runtime_dir)
    stop = asyncio.Event()
    task = asyncio.create_task(run_daemon(settings, stop_event=stop))
    try:
        for _ in range(100):
            if settings.control_socket.exists():
                break
            await asyncio.sleep(0.01)
        assert settings.control_socket.exists()
        assert not task.done()

        client = BootstrapControlClient(settings.control_socket)
        assert (await client.request("health"))["status"] == "running"
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=2)
    assert not settings.control_socket.exists()


@pytest.mark.asyncio
async def test_daemon_startup_failure_releases_single_instance_lock(
    tmp_path: Path,
    short_runtime_dir: Path,
) -> None:
    settings = _settings(
        tmp_path,
        BootstrapMode.PRODUCTION,
        runtime_dir=short_runtime_dir,
    )

    with pytest.raises(HostIdentityProvisioningRequired):
        await run_daemon(settings, stop_event=asyncio.Event())

    replacement = BootstrapInstanceLock(settings.instance_lock_path)
    replacement.acquire()
    replacement.release()


@pytest.mark.asyncio
async def test_local_api_is_a_separate_read_only_projection(
    tmp_path: Path,
    short_runtime_dir: Path,
) -> None:
    bootstrap = _settings(tmp_path, runtime_dir=short_runtime_dir)
    stop = asyncio.Event()
    daemon_task = asyncio.create_task(run_daemon(bootstrap, stop_event=stop))
    try:
        for _ in range(100):
            if bootstrap.control_socket.exists():
                break
            await asyncio.sleep(0.01)

        app = create_app(LocalApiSettings(bootstrap=bootstrap))
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://local.test",
        ) as client:
            descriptor = await client.get("/api/local/v1/descriptor")
            state = await client.get("/api/local/v1/system/state")
            mutation = await client.post("/api/local/v1/setup/initialize")

        assert descriptor.status_code == 200
        assert descriptor.json()["host_id"].startswith("ehost-")
        assert state.status_code == 200
        assert state.json()["state"]["claim_state"] == "unclaimed"
        assert mutation.status_code == 404
    finally:
        stop.set()
        await asyncio.wait_for(daemon_task, timeout=2)


def test_bootstrap_contracts_are_valid_json() -> None:
    root = Path(__file__).resolve().parents[2]
    contracts = root / "contracts" / "bootstrap" / "v1"

    documents = [json.loads(path.read_text()) for path in contracts.glob("*.json")]

    assert len(documents) == 4
    assert all(document["$schema"].endswith("2020-12/schema") for document in documents)


def test_systemd_watchdog_uses_half_interval_and_main_pid() -> None:
    notifier = SystemdNotifier.from_environ(
        {
            "NOTIFY_SOCKET": "@eidolon-test",
            "WATCHDOG_USEC": "30000000",
            "WATCHDOG_PID": str(os.getpid()),
        }
    )
    wrong_process = SystemdNotifier.from_environ(
        {
            "NOTIFY_SOCKET": "@eidolon-test",
            "WATCHDOG_USEC": "30000000",
            "WATCHDOG_PID": "999999",
        }
    )

    assert notifier.address == "@eidolon-test"
    assert notifier.watchdog_interval_seconds == 15.0
    assert wrong_process.watchdog_interval_seconds is None
