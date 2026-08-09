from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from eidolon_sdk.biz.system_data import CompanionRuntimeSnapshot

from eidolon_admin_server.bootstrap.adapters.persistence import (
    SQLiteBootstrapStateStore,
)
from eidolon_admin_server.bootstrap.adapters.network import (
    InMemoryNetworkProvisioning,
)
from eidolon_admin_server.bootstrap.commissioning_service import CommissioningService
from eidolon_admin_server.bootstrap.config import (
    BootstrapConfigurationError,
    BootstrapMode,
    BootstrapSettings,
    CommissioningAdapter,
    NetworkAdapter,
    load_bootstrap_settings,
)
from eidolon_admin_server.bootstrap.control import (
    BootstrapControlClient,
    BootstrapControlError,
    BootstrapControlServer,
)
from eidolon_admin_server.bootstrap.daemon import run_daemon
from eidolon_admin_server.bootstrap.domain import NetworkState
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
from eidolon_admin_server.app.control_plane.contracts import (
    KernelMountPage,
    WorkspaceInitializeRequest,
    WorkspaceOperation,
)
from eidolon_admin_server.local_api.app import create_app
from eidolon_admin_server.local_api.config import LocalApiSettings
from eidolon_admin_server.local_api.workspace import WorkspaceSetupError


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
        dev_setup_code_ttl_seconds=600,
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


def _service(
    settings: BootstrapSettings,
    *,
    network: InMemoryNetworkProvisioning | None = None,
) -> BootstrapService:
    return BootstrapService(
        settings=settings,
        store=SQLiteBootstrapStateStore(settings.database_path),
        identity_manager=HostIdentityManager(
            settings.identity_key_path,
            settings.mode,
        ),
        network=network,
    )


class _WorkspaceClient:
    def __init__(self) -> None:
        self.result: WorkspaceOperation | None = None
        self.initialize_calls: list[tuple[str, WorkspaceInitializeRequest]] = []

    async def initialize(
        self,
        *,
        operation_id: str,
        payload: WorkspaceInitializeRequest,
    ) -> WorkspaceOperation:
        self.initialize_calls.append((operation_id, payload))
        marker = operation_id.replace("-", "")
        result = WorkspaceOperation.model_validate(
            {
                "contract_version": "1",
                "operation": "owner-workspace.initialize",
                "operation_id": operation_id,
                "request_fingerprint": "sha256:" + "0" * 64,
                "status": "succeeded",
                "owner": {
                    "owner_id": "owner_workspace_authority",
                    "display_name": payload.owner_display_name,
                    "lifecycle_state": "active",
                },
                "workspace": {
                    "state": "ready",
                    "primary_companion_id": f"c_{marker}",
                    "persona_genome_id": f"g_{marker}_origin",
                    "memory_realm_id": f"r_{marker}",
                },
            }
        )
        if self.result is not None:
            assert result.request_fingerprint == self.result.request_fingerprint
        self.result = result
        return result

    async def get(self, operation_id: str) -> WorkspaceOperation:
        if self.result is None:
            raise WorkspaceSetupError(
                "Workspace operation does not exist",
                status_code=404,
            )
        assert self.result.operation_id == operation_id
        return self.result

    async def close(self) -> None:
        return None


class _RuntimeClient:
    def __init__(self, workspace: _WorkspaceClient) -> None:
        self.workspace = workspace
        self.calls = 0

    async def get_owner_primary_runtime(
        self,
        owner_id: str,
    ) -> CompanionRuntimeSnapshot:
        self.calls += 1
        operation = self.workspace.result
        assert operation is not None
        assert operation.owner.owner_id == owner_id
        return CompanionRuntimeSnapshot.model_validate(
            {
                "contract_version": "1",
                "operation": "companion.runtime-snapshot",
                "owner_id": owner_id,
                "companion_id": operation.workspace.primary_companion_id,
                "lifecycle_state": "active",
                "runtime_config": {},
                "memory_realm": {
                    "realm_id": operation.workspace.memory_realm_id,
                    "lifecycle_state": "active",
                },
                "persona_genome": {
                    "genome_id": operation.workspace.persona_genome_id,
                    "version": 1,
                    "lifecycle_state": "committed",
                    "schema_version": "eidolon.persona_genome",
                    "genome_hash": "sha256:" + "a" * 64,
                    "realizer_version": "1",
                    "genome": {},
                },
            }
        )

    async def close(self) -> None:
        return None


class _DevicesClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def list_mounts(self, owner_id: str) -> KernelMountPage:
        self.calls.append(owner_id)
        return KernelMountPage.model_validate(
            {
                "operation": "kernel.device-mount-page",
                "next_cursor": None,
                "mounts": [
                    {
                        "operation": "kernel.device-mount",
                        "device_id": "device-local-1",
                        "owner_id": owner_id,
                        "attached_companion_id": "companion-device-1",
                        "revision": 2,
                        "created_at": "2026-08-09T08:00:00Z",
                        "updated_at": "2026-08-09T08:10:00Z",
                        "request_id": "internal-device-request",
                        "fingerprint": "sha256:" + "0" * 64,
                        "active": True,
                    }
                ],
            }
        )

    async def close(self) -> None:
        return None


def test_bootstrap_settings_default_to_fail_closed_production() -> None:
    settings = load_bootstrap_settings({})

    assert settings.mode is BootstrapMode.PRODUCTION
    assert settings.commissioning_adapter is CommissioningAdapter.BLUEZ
    assert settings.network_adapter is NetworkAdapter.NETWORK_MANAGER


def test_development_defaults_to_hardware_free_adapters() -> None:
    settings = load_bootstrap_settings({"EIDOLON_BOOTSTRAP_MODE": "development"})

    assert settings.commissioning_adapter is CommissioningAdapter.DISABLED
    assert settings.network_adapter is NetworkAdapter.MEMORY
    assert settings.dev_setup_code is None


def test_fixed_setup_code_is_accepted_only_in_development() -> None:
    settings = load_bootstrap_settings(
        {
            "EIDOLON_BOOTSTRAP_MODE": "development",
            "EIDOLON_BOOTSTRAP_DEV_SETUP_CODE": "246810",
        }
    )
    assert settings.dev_setup_code == "246810"

    with pytest.raises(BootstrapConfigurationError, match="development-only"):
        load_bootstrap_settings(
            {
                "EIDOLON_BOOTSTRAP_MODE": "production",
                "EIDOLON_BOOTSTRAP_DEV_SETUP_CODE": "246810",
            }
        )


@pytest.mark.parametrize("code", ["12345", "1234567", "abcdef", "12 456"])
def test_fixed_development_setup_code_requires_six_digits(code: str) -> None:
    with pytest.raises(BootstrapConfigurationError, match="exactly 6 digits"):
        load_bootstrap_settings(
            {
                "EIDOLON_BOOTSTRAP_MODE": "development",
                "EIDOLON_BOOTSTRAP_DEV_SETUP_CODE": code,
            }
        )


def test_production_rejects_test_adapters() -> None:
    with pytest.raises(BootstrapConfigurationError, match="requires bluez"):
        load_bootstrap_settings(
            {
                "EIDOLON_BOOTSTRAP_MODE": "production",
                "EIDOLON_BOOTSTRAP_COMMISSIONING_ADAPTER": "disabled",
                "EIDOLON_BOOTSTRAP_NETWORK_ADAPTER": "memory",
            }
        )


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


def test_development_setup_code_is_short_lived_and_not_persisted(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    service = _service(settings)
    service.initialize()
    try:
        credential = service.issue_development_setup_code(300)
        assert settings.database_path.stat().st_mode & 0o777 == 0o600
        assert credential["host_id"].startswith("ehost-")
        assert credential["setup_code"].isdigit()
        assert len(credential["setup_code"]) == 6

        database_dump = "\n".join(
            service._store.connection.iterdump()  # noqa: SLF001 - persistence invariant
        )
        assert credential["setup_code"] not in database_dump

        replacement = service.issue_development_setup_code(300)
        assert replacement["commissioning_id"] != credential["commissioning_id"]
        status = service.development_setup_status()["current"]
        assert status["session_id"] == replacement["commissioning_id"]
        assert "setup_code" not in status
    finally:
        service.shutdown()


def test_fixed_development_setup_code_is_automatically_available(
    tmp_path: Path,
) -> None:
    settings = replace(_settings(tmp_path), dev_setup_code="246810")
    service = _service(settings)
    service.initialize()
    try:
        endpoint = service.commissioning_endpoint()
        development_setup = endpoint["development_setup"]
        assert development_setup is not None

        commissioning = CommissioningService(
            store=service._store,  # noqa: SLF001 - test application boundary
            network=InMemoryNetworkProvisioning(),
        )
        authorization = commissioning.authorize(
            session_id=development_setup["commissioning_id"],
            secret="246810",
        )
        assert authorization.session_id == development_setup["commissioning_id"]

        replacement = service.issue_development_setup_code(300)
        assert replacement["setup_code"] == "246810"
        assert replacement["commissioning_id"] != development_setup["commissioning_id"]

        database_dump = "\n".join(
            service._store.connection.iterdump()  # noqa: SLF001
        )
        assert "246810" not in database_dump
    finally:
        service.shutdown()


def test_production_rejects_development_setup_code_issuance(tmp_path: Path) -> None:
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
            service.issue_development_setup_code()
    finally:
        service.shutdown()


@pytest.mark.asyncio
async def test_production_rejects_development_reset(tmp_path: Path) -> None:
    development = _settings(tmp_path)
    HostIdentityManager(
        development.identity_key_path,
        development.mode,
    ).load()
    production = _settings(tmp_path, BootstrapMode.PRODUCTION)
    service = _service(production, network=InMemoryNetworkProvisioning())
    service.initialize()
    try:
        with pytest.raises(BootstrapOperationRejected, match="disabled"):
            await service.reset_development_state(forget_wifi_profiles=False)
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

        credential = await client.request("dev.code", ttl_seconds=300)
        assert len(credential["setup_code"]) == 6

        challenge = "ICEiIyQlJicoKSorLC0uLzAxMjM0NTY3ODk6Ozw9Pj8"
        proof = await client.request("host.prove", challenge=challenge)
        assert proof["challenge"] == challenge
        assert proof["host_id"] == health["descriptor"]["host_id"]

        with pytest.raises(BootstrapControlError, match="32 unpadded"):
            await client.request("host.prove", challenge="too-short")
        with pytest.raises(BootstrapControlError, match="canonical base64url"):
            await client.request("host.prove", challenge=challenge[:-1] + "9")

        with pytest.raises(BootstrapControlError, match="unknown control operation"):
            await client.request("not.allowed")
    finally:
        await server.close()
        service.shutdown()


@pytest.mark.asyncio
async def test_control_socket_development_reset_can_forget_wifi(
    tmp_path: Path,
    short_runtime_dir: Path,
) -> None:
    settings = replace(
        _settings(tmp_path, runtime_dir=short_runtime_dir),
        dev_setup_code="246810",
    )
    network = InMemoryNetworkProvisioning(current_ssid="Development Wi-Fi")
    service = _service(settings, network=network)
    service.initialize()
    service.reconcile_network_state(NetworkState.CONNECTED)
    server = BootstrapControlServer(settings.control_socket, service)
    await server.start()
    client = BootstrapControlClient(settings.control_socket)
    try:
        reset = await client.request("dev.reset", forget_wifi_profiles=True)
        assert reset["before"]["network_state"] == "connected"
        assert reset["after"]["claim_state"] == "unclaimed"
        assert reset["after"]["network_state"] == "unconfigured"
        assert reset["after"]["reset_epoch"] == 1
        assert reset["development_setup"] is not None
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
async def test_local_api_is_a_separate_minimal_projection_and_host_proof(
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
            host = await client.get("/api/local/v1/host")
            state = await client.get("/api/local/v1/system/state")
            proof = await client.post(
                "/api/local/v1/host/proof",
                json={
                    "contract_version": "1",
                    "challenge": "ICEiIyQlJicoKSorLC0uLzAxMjM0NTY3ODk6Ozw9Pj8",
                },
            )
            mutation = await client.post("/api/local/v1/setup/initialize")

        assert descriptor.status_code == 200
        assert descriptor.json()["host_id"].startswith("ehost-")
        assert host.status_code == 200
        assert host.json() == {
            "contract_version": "1",
            "status": "running",
            "mode": "development",
            "descriptor": descriptor.json(),
            "state": state.json()["state"],
        }
        assert state.status_code == 200
        assert state.json()["state"]["claim_state"] == "unclaimed"
        assert proof.status_code == 200
        proof_document = proof.json()
        assert proof_document["purpose"] == "eidolon-local-api-host-proof-v1"
        proof_unsigned = {
            key: value for key, value in proof_document.items() if key != "signature"
        }
        proof_canonical = json.dumps(
            proof_unsigned,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        proof_public_key = base64.urlsafe_b64decode(
            descriptor.json()["host_public_key"] + "="
        )
        proof_signature = base64.urlsafe_b64decode(proof_document["signature"] + "==")
        Ed25519PublicKey.from_public_bytes(proof_public_key).verify(
            proof_signature,
            proof_canonical,
        )
        assert mutation.status_code == 404
    finally:
        stop.set()
        await asyncio.wait_for(daemon_task, timeout=2)


@pytest.mark.asyncio
async def test_local_api_controller_session_is_one_time_and_reset_bound(
    tmp_path: Path,
    short_runtime_dir: Path,
) -> None:
    settings = _settings(tmp_path, runtime_dir=short_runtime_dir)
    store = SQLiteBootstrapStateStore(settings.database_path)
    network = InMemoryNetworkProvisioning()
    bootstrap_service = BootstrapService(
        settings=settings,
        store=store,
        identity_manager=HostIdentityManager(
            settings.identity_key_path,
            settings.mode,
        ),
        network=network,
    )
    bootstrap_service.initialize()
    commissioning = CommissioningService(store=store, network=network)
    setup = bootstrap_service.issue_development_setup_code(300)
    initial = commissioning.authorize(
        session_id=setup["commissioning_id"],
        secret=setup["setup_code"],
    )
    operation_id = "9a6bc772-86f7-4ace-a022-ecb9cb8df114"
    await commissioning.configure_network(
        initial,
        {"operation_id": operation_id, "ssid": "Existing Home"},
    )
    await commissioning.confirm_network(initial, operation_id)
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_der = private_key.public_key().public_bytes(
        Encoding.DER,
        PublicFormat.SubjectPublicKeyInfo,
    )
    encoded_public = base64.urlsafe_b64encode(public_der).rstrip(b"=").decode()
    controller_id = f"ectrl-{hashlib.sha256(public_der).hexdigest()[:20]}"
    commissioning.claim_controller(
        initial,
        {
            "controller_id": controller_id,
            "public_key": encoded_public,
            "display_name": "Primary phone",
            "platform": "android",
        },
    )
    bootstrap_service.shutdown()

    stop = asyncio.Event()
    daemon_task = asyncio.create_task(run_daemon(settings, stop_event=stop))
    try:
        for _ in range(100):
            if settings.control_socket.exists():
                break
            await asyncio.sleep(0.01)

        workspace_client = _WorkspaceClient()
        runtime_client = _RuntimeClient(workspace_client)
        devices_client = _DevicesClient()
        app = create_app(
            LocalApiSettings(bootstrap=settings),
            workspace_client=workspace_client,
            runtime_client=runtime_client,
            devices_client=devices_client,
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="https://local.test",
        ) as client:
            challenge_response = await client.post(
                "/api/local/v1/auth/challenges",
                json={
                    "contract_version": "1",
                    "controller_id": controller_id,
                },
            )
            assert challenge_response.status_code == 200
            challenge = challenge_response.json()
            assert challenge["purpose"] == "eidolon-controller-local-auth-v1"
            canonical = json.dumps(
                challenge,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
            signature = private_key.sign(canonical, ec.ECDSA(hashes.SHA256()))
            proof = {
                **challenge,
                "signature": base64.urlsafe_b64encode(signature).rstrip(b"=").decode(),
            }
            session_response = await client.post(
                "/api/local/v1/auth/sessions",
                json=proof,
            )
            assert session_response.status_code == 200
            session = session_response.json()
            assert session["token_type"] == "Bearer"
            assert session["controller"]["controller_id"] == controller_id
            token = session["access_token"]
            assert len(token) == 43

            replay = await client.post(
                "/api/local/v1/auth/sessions",
                json=proof,
            )
            assert replay.status_code == 401
            missing = await client.get("/api/local/v1/auth/session")
            assert missing.status_code == 401
            current = await client.get(
                "/api/local/v1/auth/session",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert current.status_code == 200
            assert current.json()["controller"]["reset_epoch"] == 0
            assert current.json()["controller"]["owner_id"] is None

            workspace_headers = {"Authorization": f"Bearer {token}"}
            absent = await client.get(
                "/api/local/v1/setup/workspace",
                headers=workspace_headers,
            )
            assert absent.status_code == 200
            assert absent.json()["state"] == "absent"
            runtime_before_workspace = await client.get(
                "/api/local/v1/workspace/runtime",
                headers=workspace_headers,
            )
            assert runtime_before_workspace.status_code == 409
            assert runtime_client.calls == 0
            devices_before_workspace = await client.get(
                "/api/local/v1/devices",
                headers=workspace_headers,
            )
            assert devices_before_workspace.status_code == 409
            assert devices_client.calls == []

            unauthenticated = await client.put(
                "/api/local/v1/setup/workspace",
                json={"owner_display_name": "Manson"},
            )
            assert unauthenticated.status_code == 401
            initialized = await client.put(
                "/api/local/v1/setup/workspace",
                headers=workspace_headers,
                json={
                    "owner_display_name": "Manson",
                    "companion_display_name": "Eidolon",
                },
            )
            assert initialized.status_code == 200
            assert initialized.json()["state"] == "ready"
            assert initialized.json()["owner"]["owner_id"] == (
                "owner_workspace_authority"
            )
            replay = await client.put(
                "/api/local/v1/setup/workspace",
                headers=workspace_headers,
                json={"owner_display_name": "Manson"},
            )
            assert replay.status_code == 200
            assert replay.json() == initialized.json()
            assert len(workspace_client.initialize_calls) == 1

            ready = await client.get(
                "/api/local/v1/setup/workspace",
                headers=workspace_headers,
            )
            assert ready.json() == initialized.json()

            runtime = await client.get(
                "/api/local/v1/workspace/runtime",
                headers=workspace_headers,
            )
            assert runtime.status_code == 200
            assert runtime.json()["owner"]["owner_id"] == ("owner_workspace_authority")
            assert (
                runtime.json()["primary_companion"]["companion_id"]
                == (initialized.json()["workspace"]["primary_companion_id"])
            )
            assert runtime_client.calls == 1
            devices = await client.get(
                "/api/local/v1/devices",
                headers=workspace_headers,
            )
            assert devices.status_code == 200
            assert devices.json()["coverage"] == "mounted-devices"
            assert devices.json()["devices"][0] == {
                "device_id": "device-local-1",
                "admission_state": "ready",
                "mount": {
                    "state": "active",
                    "revision": 2,
                    "attached_companion_id": "companion-device-1",
                    "updated_at": "2026-08-09T08:10:00Z",
                },
            }
            assert devices_client.calls == ["owner_workspace_authority"]
            device = await client.get(
                "/api/local/v1/devices/device-local-1",
                headers=workspace_headers,
            )
            assert device.status_code == 200
            assert device.json()["device_id"] == "device-local-1"

            restarted_app = create_app(
                LocalApiSettings(bootstrap=settings),
                workspace_client=workspace_client,
                runtime_client=runtime_client,
                devices_client=devices_client,
            )
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=restarted_app),
                base_url="https://local-restarted.test",
            ) as restarted:
                expired_on_restart = await restarted.get(
                    "/api/local/v1/auth/session",
                    headers=workspace_headers,
                )
                assert expired_on_restart.status_code == 401

                next_challenge_response = await restarted.post(
                    "/api/local/v1/auth/challenges",
                    json={
                        "contract_version": "1",
                        "controller_id": controller_id,
                    },
                )
                assert next_challenge_response.status_code == 200
                next_challenge = next_challenge_response.json()
                next_signature = private_key.sign(
                    json.dumps(
                        next_challenge,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode(),
                    ec.ECDSA(hashes.SHA256()),
                )
                next_session = await restarted.post(
                    "/api/local/v1/auth/sessions",
                    json={
                        **next_challenge,
                        "signature": base64.urlsafe_b64encode(next_signature)
                        .rstrip(b"=")
                        .decode(),
                    },
                )
                assert next_session.status_code == 200
                next_headers = {
                    "Authorization": (f"Bearer {next_session.json()['access_token']}")
                }
                resumed_workspace = await restarted.get(
                    "/api/local/v1/setup/workspace",
                    headers=next_headers,
                )
                resumed_runtime = await restarted.get(
                    "/api/local/v1/workspace/runtime",
                    headers=next_headers,
                )
                assert resumed_workspace.json() == initialized.json()
                assert resumed_runtime.status_code == 200

            control = BootstrapControlClient(settings.control_socket)
            refreshed = await client.get(
                "/api/local/v1/auth/session",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert refreshed.status_code == 200
            assert (
                refreshed.json()["controller"]["owner_id"]
                == "owner_workspace_authority"
            )
            with pytest.raises(BootstrapControlError, match="another Owner"):
                await control.request(
                    "controller.bind_owner",
                    controller_id=controller_id,
                    reset_epoch=0,
                    owner_id="owner_conflict",
                )

            await control.request("dev.reset")
            invalidated = await client.get(
                "/api/local/v1/auth/session",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert invalidated.status_code == 401
    finally:
        stop.set()
        await asyncio.wait_for(daemon_task, timeout=2)


def test_bootstrap_contracts_are_valid_json() -> None:
    root = Path(__file__).resolve().parents[2]
    contracts = root / "contracts" / "bootstrap" / "v1"

    documents = [json.loads(path.read_text()) for path in contracts.glob("*.json")]

    assert len(documents) == 6
    assert all(document["$schema"].endswith("2020-12/schema") for document in documents)

    local_api_contract = json.loads(
        (
            root / "contracts" / "local-api" / "v1" / "host-overview.schema.json"
        ).read_text()
    )
    assert local_api_contract["properties"]["descriptor"]["$ref"].endswith(
        "public-descriptor.schema.json"
    )
    local_api_documents = [
        json.loads(path.read_text())
        for path in (root / "contracts" / "local-api" / "v1").glob("*.json")
    ]
    assert len(local_api_documents) == 12
    assert all(
        document["$schema"].endswith("2020-12/schema")
        for document in local_api_documents
    )


def test_host_proof_example_is_a_valid_cross_language_vector() -> None:
    root = Path(__file__).resolve().parents[2]
    public_key = base64.urlsafe_b64decode(
        "A6EHv_POEL4dcN0Y50vAmWfk1jCbpQ1fHdyGZBJVMbg="
    )
    proof = json.loads(
        (
            root / "contracts" / "local-api" / "v1" / "examples" / "host-proof.json"
        ).read_text()
    )
    proof_unsigned = {key: value for key, value in proof.items() if key != "signature"}
    proof_canonical = json.dumps(
        proof_unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    proof_signature = base64.urlsafe_b64decode(proof["signature"] + "==")
    Ed25519PublicKey.from_public_bytes(public_key).verify(
        proof_signature,
        proof_canonical,
    )


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
