from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
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
from eidolon_admin_server.bootstrap.domain import SETUP_CODE_DIGITS
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
    CompanionFace,
    CompanionIdentity,
    HubDevice,
    OwnerDeviceHistory,
    OwnerIdentity,
    OwnerInventory,
    PersonaChapter,
    PersonaTimeline,
    KernelMountPage,
    WorkspaceInitializeRequest,
    WorkspaceOperation,
)
from eidolon_admin_server.app.control_plane.workspace_policy import (
    workspace_request_fingerprint,
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
                "request_fingerprint": workspace_request_fingerprint(payload),
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
        self.renamed: tuple[str, str] | None = None
        self.renamed_owner: tuple[str, str] | None = None
        self.face: bytes | None = None
        self.recalled: tuple[str, str, int] | None = None
        self.restored: tuple[str, str] | None = None
        self.owner_of_companion: str | None = None

    async def get_owner_default_runtime(
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


    async def get_persona_timeline(self, companion_id: str) -> PersonaTimeline:
        return PersonaTimeline(
            operation="companion.persona-timeline",
            companion_id=companion_id,
            chapters=(
                PersonaChapter(
                    genome_id="g_2",
                    version=2,
                    lifecycle_state="proposed",
                    change_summary="想变得更安静一些",
                    created_at="2026-08-16T10:00:00Z",
                ),
                PersonaChapter(
                    genome_id="g_1",
                    version=1,
                    lifecycle_state="committed",
                    change_summary="",
                    is_current=True,
                    created_at="2026-08-09T08:00:00Z",
                ),
            ),
        )

    async def restore_persona(
        self,
        companion_id: str,
        genome_id: str,
        change_summary: str,
    ) -> PersonaChapter:
        self.restored = (genome_id, change_summary)
        return PersonaChapter(
            genome_id="g_3",
            version=3,
            lifecycle_state="committed",
            change_summary=change_summary,
            restored_from_version=1,
            is_current=True,
            created_at="2026-08-16T11:00:00Z",
        )

    async def rename_companion(
        self,
        companion_id: str,
        display_name: str,
    ) -> CompanionIdentity:
        self.renamed = (companion_id, display_name)
        return CompanionIdentity(
            operation="companion.identity",
            companion_id=companion_id,
            owner_id=self.workspace.result.owner.owner_id,
            display_name=display_name,
            lifecycle_state="active",
        )

    async def get_companion_face_state(self, companion_id: str) -> CompanionFace:
        return self._face(companion_id)

    async def get_companion_face(self, companion_id: str) -> bytes | None:
        return self.face

    async def set_companion_face(self, companion_id: str, face: bytes) -> CompanionFace:
        self.face = face
        return self._face(companion_id)

    async def clear_companion_face(self, companion_id: str) -> CompanionFace:
        self.face = None
        return self._face(companion_id)

    def _face(self, companion_id: str) -> CompanionFace:
        if self.face is None:
            return CompanionFace(
                operation="companion.face",
                companion_id=companion_id,
                has_face=False,
            )
        return CompanionFace(
            operation="companion.face",
            companion_id=companion_id,
            has_face=True,
            face_asset_id="face-1",
            sha256=hashlib.sha256(self.face).hexdigest(),
            size_bytes=len(self.face),
            updated_at="2026-08-17T09:00:00Z",
        )

    async def recollections(
        self,
        owner_id: str,
        query: str,
        limit: int,
    ) -> list[dict]:
        self.recalled = (owner_id, query, limit)
        return [
            {
                "text": "他喜欢在下午散步",
                "metadata": {
                    "created_at": "2026-08-16T09:30:00Z",
                    "wing": "episodic",
                    "room": "walks",
                    "score": 0.82,
                },
            },
            {"text": "没有元数据的那一条"},
        ]

    async def rename_owner(
        self,
        owner_id: str,
        display_name: str,
    ) -> OwnerIdentity:
        self.renamed_owner = (owner_id, display_name)
        return OwnerIdentity(
            operation="owner.identity",
            revision=1,
            owner_id=owner_id,
            display_name=display_name,
            lifecycle_state="active",
        )

    async def get_companion(self, companion_id: str) -> CompanionIdentity:
        # The name a person gave this Eidolon lives with its identity, not in
        # the snapshot that says how to run it.
        return CompanionIdentity(
            operation="companion.identity",
            companion_id=companion_id,
            owner_id=(
                self.owner_of_companion or self.workspace.result.owner.owner_id
            ),
            display_name="小忆",
            lifecycle_state="active",
        )

class _DevicesClient:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.history_calls: list[tuple[str, str, int]] = []
        #: Set to make the history unreadable, which must not read as empty.
        self.history_failure: Exception | None = None
        self.renamed: tuple[str, str] | None = None
        #: Set to None to answer as a Host whose directory could not be
        #: reached: the devices are still theirs, the names are simply gone.
        self.directory: list[dict] | None = [
            {
                "operation": "device.directory-entry",
                "device_id": "device-local-1",
                "owner_scope": "owner_workspace_authority",
                "display_name": "客厅的 Box-3",
                "device_kind": "esp32-box3",
                "manifest": {
                    "schema_version": 1,
                    "title": "Box-3",
                    "properties": [],
                    "actions": [],
                    "events": [],
                    "media": [],
                },
                "manifest_revision": "sha256:" + "a" * 64,
                "device_ref": {
                    "device_instance_id": "device-local-1",
                    "owner_domain_id": "owner_workspace_authority",
                    "owner_domain_generation": 1,
                    "claim_generation": 1,
                    "trust_epoch": 1,
                    "accepted_manifest_digest": "sha256:" + "a" * 64,
                },
                "lifecycle_state": "approved",
                "enrolled_at": "2026-08-09T08:00:00Z",
                "updated_at": "2026-08-09T08:10:00Z",
            }
        ]

    async def rename(
        self,
        owner_id: str,
        controller_id: str,
        device_id: str,
        display_name: str,
    ):
        self.renamed = (device_id, display_name)
        assert self.directory is not None
        for entry in self.directory:
            if entry["device_id"] == device_id:
                entry["display_name"] = display_name
                return HubDevice.model_validate(entry)
        raise AssertionError("renamed a device this Host does not hold")

    async def list_history(self, owner_id: str, controller_id: str, limit: int):
        self.history_calls.append((owner_id, controller_id, limit))
        if self.history_failure is not None:
            raise self.history_failure
        return OwnerDeviceHistory.model_validate(
            {
                "operation": "admin.owner-device-history",
                "owner_id": owner_id,
                "events": [
                    {
                        "operation": "device.management-event",
                        "stream_position": 2,
                        "event_id": "evt-approved",
                        "event_type": "eidolon.device.approved.v1",
                        "source": "eidolon-hub/device-management",
                        "principal_id": f"eidolon-local-api/{controller_id}",
                        "device_id": "device-local-1",
                        "occurred_at": "2026-08-17T10:14:40Z",
                        "data": {"owner_id": owner_id},
                    },
                    {
                        "operation": "device.management-event",
                        "stream_position": 1,
                        "event_id": "evt-enrolled",
                        "event_type": "eidolon.device.enrolled.v1",
                        "source": "eidolon-hub/device-management",
                        "principal_id": "untrusted-device:device-local-1",
                        "device_id": "device-local-1",
                        "occurred_at": "2026-08-17T10:06:15Z",
                        "data": {"manifest_revision": "sha256:" + "a" * 64},
                    },
                ],
                "devices": self.directory or [],
            }
        )

    async def list_inventory(self, owner_id: str, controller_id: str):
        mounts = await self.list_mounts(owner_id)
        return OwnerInventory.model_validate(
            {
                "operation": "admin.owner-device-inventory",
                "owner_id": owner_id,
                "degraded": self.directory is None,
                "hub": {
                    "state": "ok" if self.directory is not None else "error",
                    "latency_ms": 1.0,
                },
                "kernel": {"state": "ok", "latency_ms": 1.0},
                "devices": self.directory or [],
                "mounts": [mount.model_dump(mode="json") for mount in mounts.mounts],
            }
        )

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
                        "device_ref": {
                            "device_instance_id": "device-local-1",
                            "owner_domain_id": owner_id,
                            "owner_domain_generation": 1,
                            "claim_generation": 1,
                            "trust_epoch": 1,
                            "accepted_manifest_digest": "sha256:" + "a" * 64,
                        },
                        "attached_companion_id": "companion-device-1",
                        "revision": 2,
                        "created_at": "2026-08-09T08:00:00Z",
                        "updated_at": "2026-08-09T08:10:00Z",
                        "request_id": "internal-device-request",
                        "fingerprint": "sha256:" + "0" * 64,
                        "active": True,
                    },
                    {
                        "operation": "kernel.device-mount",
                        "device_id": "device-local-removed",
                        "owner_id": owner_id,
                        "device_ref": {
                            "device_instance_id": "device-local-removed",
                            "owner_domain_id": owner_id,
                            "owner_domain_generation": 1,
                            "claim_generation": 1,
                            "trust_epoch": 1,
                            "accepted_manifest_digest": "sha256:" + "b" * 64,
                        },
                        "attached_companion_id": None,
                        "revision": 4,
                        "created_at": "2026-08-09T08:00:00Z",
                        "updated_at": "2026-08-09T08:20:00Z",
                        "request_id": "internal-removal-request",
                        "fingerprint": "sha256:" + "1" * 64,
                        "active": False,
                    },
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
            "EIDOLON_BOOTSTRAP_DEV_SETUP_CODE": "24681012",
        }
    )
    assert settings.dev_setup_code == "24681012"

    with pytest.raises(BootstrapConfigurationError, match="development-only"):
        load_bootstrap_settings(
            {
                "EIDOLON_BOOTSTRAP_MODE": "production",
                "EIDOLON_BOOTSTRAP_DEV_SETUP_CODE": "24681012",
            }
        )


@pytest.mark.parametrize(
    "code",
    [
        "1234567",  # too short
        "123456789",  # too long
        "abcdefgh",
        "12 45678",
        "11111111",  # every digit the same
        "01234567",  # the plain run up
        "76543210",  # and down
    ],
)
def test_a_fixed_setup_code_must_be_one_nobody_would_guess(code: str) -> None:
    with pytest.raises(BootstrapConfigurationError, match="usable 8-digit Setup code"):
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
        credential = service.issue_setup_code(300)
        assert settings.database_path.stat().st_mode & 0o777 == 0o600
        assert credential["host_id"].startswith("ehost-")
        assert credential["setup_code"].isdigit()
        assert len(credential["setup_code"]) == SETUP_CODE_DIGITS

        database_dump = "\n".join(
            service._store.connection.iterdump()  # noqa: SLF001 - persistence invariant
        )
        assert credential["setup_code"] not in database_dump

        replacement = service.issue_setup_code(300)
        assert replacement["commissioning_id"] != credential["commissioning_id"]
        status = service.setup_session_status()["current"]
        assert status["session_id"] == replacement["commissioning_id"]
        assert "setup_code" not in status
    finally:
        service.shutdown()


def test_fixed_development_setup_code_is_automatically_available(
    tmp_path: Path,
) -> None:
    settings = replace(_settings(tmp_path), dev_setup_code="24681012")
    service = _service(settings)
    service.initialize()
    try:
        endpoint = service.commissioning_endpoint()
        setup_session = endpoint["setup_session"]
        assert setup_session is not None

        commissioning = CommissioningService(
            store=service._store,  # noqa: SLF001 - test application boundary
            network=InMemoryNetworkProvisioning(),
        )
        authorization = commissioning.authorize(
            session_id=setup_session["commissioning_id"],
            secret="24681012",
        )
        assert authorization.session_id == setup_session["commissioning_id"]

        replacement = service.issue_setup_code(300)
        assert replacement["setup_code"] == "24681012"
        assert replacement["commissioning_id"] != setup_session["commissioning_id"]

        database_dump = "\n".join(
            service._store.connection.iterdump()  # noqa: SLF001
        )
        assert "24681012" not in database_dump
    finally:
        service.shutdown()


def test_a_production_host_can_be_claimed_at_all(tmp_path: Path) -> None:
    """The gate is the Host's own control socket, not the build being a
    development one.

    Issuance used to be refused unless the process was in development mode,
    and nothing else could create a commissioning session — so a shipped Host
    could not be claimed by any phone, ever. Reaching this operation already
    means holding a root-owned local socket, which is the same authority
    controller-reset runs under and the greater act of the two.
    """

    development = _settings(tmp_path)
    HostIdentityManager(
        development.identity_key_path,
        development.mode,
    ).load()
    production = _settings(tmp_path, BootstrapMode.PRODUCTION)
    service = _service(production)
    service.initialize()
    try:
        credential = service.issue_setup_code(300)

        assert len(credential["setup_code"]) == SETUP_CODE_DIGITS
        assert credential["host_id"].startswith("ehost-")
        assert credential["commissioning_id"]

        # A production Host draws a fresh code each time; only a development
        # one may pin a fixed one.
        assert service.issue_setup_code(300)["setup_code"] != credential["setup_code"]

        # The development-only LAN shortcut stays development-only: it skips
        # the pinned TLS the phone would otherwise verify.
        with pytest.raises(BootstrapOperationRejected, match="LAN commissioning"):
            service.development_lan_commissioning_endpoint()
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

        credential = await client.request("commissioning.code", ttl_seconds=300)
        assert len(credential["setup_code"]) == SETUP_CODE_DIGITS

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
        dev_setup_code="24681012",
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
        assert reset["setup_session"] is not None
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

        control = BootstrapControlClient(bootstrap.control_socket)
        setup = await control.request("commissioning.code", ttl_seconds=300)
        private_key = ec.generate_private_key(ec.SECP256R1())
        public_der = private_key.public_key().public_bytes(
            Encoding.DER,
            PublicFormat.SubjectPublicKeyInfo,
        )
        encoded_public = base64.urlsafe_b64encode(public_der).rstrip(b"=").decode()
        controller_id = f"ectrl-{hashlib.sha256(public_der).hexdigest()[:20]}"
        controller = {
            "controller_id": controller_id,
            "public_key": encoded_public,
            "display_name": "Mac development Pad",
            "platform": "android",
        }

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
            commissioning_endpoint = await client.get(
                "/api/local/v1/development/commissioning/endpoint"
            )
            rejected_claim = await client.put(
                "/api/local/v1/development/commissioning/claim",
                json={
                    "contract_version": "1",
                    "commissioning_id": setup["commissioning_id"],
                    "setup_code": (
                        "00000012" if setup["setup_code"] != "00000012" else "99999987"
                    ),
                    "controller": controller,
                },
            )
            accepted_claim = await client.put(
                "/api/local/v1/development/commissioning/claim",
                json={
                    "contract_version": "1",
                    "commissioning_id": setup["commissioning_id"],
                    "setup_code": setup["setup_code"],
                    "controller": controller,
                },
            )

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
        assert commissioning_endpoint.status_code == 200
        assert commissioning_endpoint.json()["purpose"] == (
            "eidolon-ble-commissioning-endpoint-v1"
        )
        assert rejected_claim.status_code == 401
        assert accepted_claim.status_code == 200
        assert accepted_claim.json()["host_id"] == descriptor.json()["host_id"]
        assert accepted_claim.json()["controller"]["controller_id"] == controller_id
        assert accepted_claim.json()["state"]["claim_state"] == "claimed"
        assert accepted_claim.json()["state"]["network_state"] == "connected"
    finally:
        stop.set()
        await asyncio.wait_for(daemon_task, timeout=2)


@asynccontextmanager
async def _local_api_session(tmp_path: Path, runtime_dir: Path):
    """A Local API with a Controller already authenticated against it.

    Extracted because every Owner-domain slice needs exactly this and nothing
    else: a Host that has been set up, a phone that holds it, and a session to
    ask questions with. Rebuilding it per test would make each new capability
    cost a hundred lines before the first assertion.
    """

    settings = _settings(tmp_path, runtime_dir=runtime_dir)
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
    setup = bootstrap_service.issue_setup_code(300)
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
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://local.test",
        ) as client:
            challenge = (
                await client.post(
                    "/api/local/v1/auth/challenges",
                    json={"contract_version": "1", "controller_id": controller_id},
                )
            ).json()
            canonical = json.dumps(
                challenge, sort_keys=True, separators=(",", ":")
            ).encode()
            signature = private_key.sign(canonical, ec.ECDSA(hashes.SHA256()))
            session = (
                await client.post(
                    "/api/local/v1/auth/sessions",
                    json={
                        **challenge,
                        "signature": base64.urlsafe_b64encode(signature)
                        .rstrip(b"=")
                        .decode(),
                    },
                )
            ).json()
            headers = {"Authorization": f"Bearer {session['access_token']}"}
            initialized = await client.put(
                "/api/local/v1/setup/workspace",
                headers=headers,
                json={
                    "owner_display_name": "Manson",
                    "companion_display_name": "小忆",
                },
            )
            assert initialized.status_code == 200, initialized.text
            yield client, headers, runtime_client, devices_client
    finally:
        stop.set()
        await daemon_task


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
    setup = bootstrap_service.issue_setup_code(300)
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
            # The name a person gave this Eidolon reaches the Owner's own view.
            # It was stored at onboarding and, until now, never read back — the
            # product showed an identifier where someone had said what to call it.
            assert runtime.json()["primary_companion"]["display_name"] == "小忆"
            assert runtime_client.calls == 1
            devices = await client.get(
                "/api/local/v1/devices",
                headers=workspace_headers,
            )
            assert devices.status_code == 200
            assert devices.json()["coverage"] == "mounted-devices"
            assert devices.json()["devices"][0] == {
                "device_id": "device-local-1",
                # What it is, as its Owner would recognise it. The queue this
                # device came from had been calling it 客厅的 Box-3 all along;
                # once adopted, the list forgot and showed …local-1.
                "display_name": "客厅的 Box-3",
                "device_kind": "esp32-box3",
                "admission_state": "ready",
                "mount": {
                    "revision": 2,
                    "attached_companion_id": "companion-device-1",
                    "updated_at": "2026-08-09T08:10:00Z",
                },
            }
            assert devices_client.calls == ["owner_workspace_authority"]
            # The Kernel still holds the removed device's mount record; the
            # Owner-facing inventory is mounted devices only.
            assert [item["device_id"] for item in devices.json()["devices"]] == [
                "device-local-1"
            ]
            device = await client.get(
                "/api/local/v1/devices/device-local-1",
                headers=workspace_headers,
            )
            assert device.status_code == 200
            assert device.json()["device_id"] == "device-local-1"
            removed = await client.get(
                "/api/local/v1/devices/device-local-removed",
                headers=workspace_headers,
            )
            assert removed.status_code == 404

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
    assert len(local_api_documents) == 16
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


@pytest.mark.asyncio
async def test_controller_reset_lets_a_new_phone_claim_a_production_host(
    tmp_path: Path,
) -> None:
    """The Owner lost every managing phone; recovery must not cost them data."""

    development = _settings(tmp_path)
    HostIdentityManager(development.identity_key_path, development.mode).load()
    settings = replace(_settings(tmp_path), dev_setup_code="13579024")
    store = SQLiteBootstrapStateStore(settings.database_path)
    service = BootstrapService(
        settings=settings,
        store=store,
        identity_manager=HostIdentityManager(settings.identity_key_path, settings.mode),
        network=InMemoryNetworkProvisioning(),
    )
    service.initialize()
    service.reconcile_network_state(NetworkState.CONNECTED)
    issued = service.issue_setup_code()
    commissioning = CommissioningService(store=store, network=InMemoryNetworkProvisioning())
    authorization = commissioning.authorize(
        session_id=issued["commissioning_id"],
        secret=issued["setup_code"],
    )
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_der = private_key.public_key().public_bytes(
        Encoding.DER,
        PublicFormat.SubjectPublicKeyInfo,
    )
    encoded_public = base64.urlsafe_b64encode(public_der).rstrip(b"=").decode()
    controller_id = f"ectrl-{hashlib.sha256(public_der).hexdigest()[:20]}"
    commissioning.claim_controller(
        authorization,
        {
            "controller_id": controller_id,
            "public_key": encoded_public,
            "display_name": "Lost phone",
            "platform": "android",
        },
    )
    store.bind_controller_owner(
        controller_id=controller_id,
        owner_id="owner-1",
        reset_epoch=0,
        now="2026-08-11T00:00:00Z",
    )

    # A production Host refuses the development reset but must still recover.
    production = BootstrapService(
        settings=replace(settings, mode=BootstrapMode.PRODUCTION),
        store=store,
        identity_manager=HostIdentityManager(
            settings.identity_key_path, BootstrapMode.PRODUCTION
        ),
        network=InMemoryNetworkProvisioning(),
    )
    try:
        with pytest.raises(BootstrapOperationRejected, match="disabled"):
            await production.reset_development_state(forget_wifi_profiles=False)

        result = production.reset_controllers()
        host_id = production.public_descriptor()["host_id"]
        revoked_grant = store.get_controller(controller_id)
        preserved_owner = store.get_state().owner_id
    finally:
        service.shutdown()

    assert result["revoked_controllers"] == [controller_id]
    assert result["before"]["claim_state"] == "claimed"
    assert result["after"]["claim_state"] == "unclaimed"
    assert result["after"]["reset_epoch"] == result["before"]["reset_epoch"] + 1
    # Recovery replaces the manager, not the Host and not the Owner's data.
    assert preserved_owner == "owner-1"
    assert result["after"]["network_state"] == result["before"]["network_state"]
    assert result["host_id"] == host_id
    assert revoked_grant.revoked_at is not None


@pytest.mark.asyncio
async def test_controller_reset_is_reachable_over_the_control_socket(
    tmp_path: Path, short_runtime_dir: Path
) -> None:
    settings = _settings(tmp_path, runtime_dir=short_runtime_dir)
    service = _service(settings, network=InMemoryNetworkProvisioning())
    service.initialize()
    server = BootstrapControlServer(settings.control_socket, service)
    await server.start()
    client = BootstrapControlClient(settings.control_socket)
    try:
        result = await client.request("controller.reset")
        assert result["revoked_controllers"] == []
        assert result["after"]["claim_state"] == "unclaimed"
        assert "component_data" in result["preserved"]
    finally:
        await server.close()
        service.shutdown()


def test_the_endpoint_says_where_this_host_answers(monkeypatch) -> None:
    """A Host that can only be found by announcement cannot be found at all
    on a network that does not carry them to the phone in front of it.

    So it says so over the channel that does not need the network: signed with
    its identity, every address it has, because it does not know which one the
    phone can reach. Nothing is trusted for being published — whatever answers
    still proves it is this Host.
    """

    from eidolon_admin_server.bootstrap import host_addresses

    monkeypatch.setattr(
        host_addresses,
        "_kernel_reported_addresses",
        lambda: ["127.0.0.1", "169.254.181.137", "192.168.3.206"],
    )

    urls = host_addresses.local_api_base_urls(9002)

    # Loopback is not somewhere a phone can reach this Host.
    assert urls == [
        "https://192.168.3.206:9002",
        "https://169.254.181.137:9002",
    ]


def test_a_host_that_cannot_read_its_own_addresses_publishes_none(monkeypatch) -> None:
    # Saying nothing leaves the phone to look elsewhere; saying something wrong
    # sends it somewhere that will never answer.
    from eidolon_admin_server.bootstrap import host_addresses

    monkeypatch.setattr(host_addresses, "_kernel_reported_addresses", lambda: [])

    assert host_addresses.local_api_base_urls(9002) == []


@pytest.mark.asyncio
async def test_an_owner_names_their_own_companion_and_only_their_own(
    tmp_path: Path,
    short_runtime_dir: Path,
) -> None:
    """Renaming carries an id, so the id has to be checked against the session.

    An Owner will have more than one Companion, so the path names which. That
    makes "a valid session plus somebody else's identifier" a reachable
    request, and the boundary that knows whose session this is has to refuse
    it — not the control plane beneath, which knows only that Admin asked.
    """

    async with _local_api_session(tmp_path, short_runtime_dir) as (
        client,
        headers,
        runtime_client,
        _devices_client,
    ):
        companion_id = "c_11111111111111111111111111111111"

        renamed = await client.patch(
            f"/api/local/v1/companions/{companion_id}",
            json={"contract_version": "1", "display_name": "小忆"},
            headers=headers,
        )

        assert renamed.status_code == 200
        assert renamed.json()["display_name"] == "小忆"
        assert runtime_client.renamed == (companion_id, "小忆")

        # The same session, a Companion belonging to somebody else.
        runtime_client.renamed = None
        runtime_client.owner_of_companion = "owner-somebody-else"
        refused = await client.patch(
            f"/api/local/v1/companions/{companion_id}",
            json={"contract_version": "1", "display_name": "小忆"},
            headers=headers,
        )

        # Answered as absent rather than forbidden: a session that does not
        # hold this Companion learns nothing about whether it exists.
        assert refused.status_code == 404
        assert runtime_client.renamed is None


@pytest.mark.asyncio
async def test_an_owner_can_see_what_happened_to_their_devices(
    tmp_path: Path,
    short_runtime_dir: Path,
) -> None:
    """The history is the session's own, and it is asked for by nobody's id."""

    async with _local_api_session(tmp_path, short_runtime_dir) as (
        client,
        headers,
        runtime_client,
        devices_client,
    ):
        answered = await client.get("/api/local/v1/activity", headers=headers)

        assert answered.status_code == 200
        body = answered.json()
        assert body["coverage"] == "device-lifecycle"
        assert [(item["kind"], item["actor"]) for item in body["moments"]] == [
            ("device-accepted", "owner"),
            ("device-knocked", "device"),
        ]
        # The whole shape, field for field. The client that reads it lives in
        # another repository (eidolon_client_mobile, mission_control_test.dart
        # pins this same body), so a field renamed on one side has to fail on
        # one of the two rather than only on somebody's phone.
        assert body["moments"][0] == {
            "event_id": "evt-approved",
            "occurred_at": "2026-08-17T10:14:40Z",
            "kind": "device-accepted",
            "actor": "owner",
            "device_id": "device-local-1",
            # A device is carried by the name its Owner knows it by.
            "device_name": "客厅的 Box-3",
            "device_kind": "esp32-box3",
            "reason": "",
            "event_type": "eidolon.device.approved.v1",
        }

        owner_id, controller_id, limit = devices_client.history_calls[-1]
        assert owner_id == runtime_client.workspace.result.owner.owner_id
        assert controller_id.startswith("ectrl-")
        assert limit == 50

        # An unbounded history is refused here rather than passed down.
        assert (
            await client.get(
                "/api/local/v1/activity",
                params={"limit": 500},
                headers=headers,
            )
        ).status_code == 422
        assert (await client.get("/api/local/v1/activity")).status_code == 401


@pytest.mark.asyncio
async def test_a_history_that_could_not_be_read_never_reads_as_nothing_happened(
    tmp_path: Path,
    short_runtime_dir: Path,
) -> None:
    """The whole point of this screen: silence and absence are different answers."""

    from eidolon_admin_server.local_api.devices import DeviceInventoryError

    async with _local_api_session(tmp_path, short_runtime_dir) as (
        client,
        headers,
        _runtime_client,
        devices_client,
    ):
        devices_client.history_failure = DeviceInventoryError(
            "Admin Device history control plane is unavailable"
        )

        answered = await client.get("/api/local/v1/activity", headers=headers)

        assert answered.status_code == 503
        assert "moments" not in answered.text


@pytest.mark.asyncio
async def test_asking_what_it_remembers_answers_in_sentences(
    tmp_path: Path,
    short_runtime_dir: Path,
) -> None:
    """The memory asked about is the session's own, and never named by a client."""

    async with _local_api_session(tmp_path, short_runtime_dir) as (
        client,
        headers,
        runtime_client,
        _devices_client,
    ):
        answered = await client.get(
            "/api/local/v1/recollections",
            params={"q": "散步"},
            headers=headers,
        )

        assert answered.status_code == 200
        body = answered.json()
        assert body["query"] == "散步"
        assert body["recollections"] == [
            {
                "text": "他喜欢在下午散步",
                "remembered_at": "2026-08-16T09:30:00Z",
            },
            {"text": "没有元数据的那一条", "remembered_at": None},
        ]
        # Wings, rooms and scores are how memory found something, not what it
        # remembers, and a person asked the second question.
        assert "wing" not in answered.text
        assert "score" not in answered.text

        owner_id, query, limit = runtime_client.recalled
        assert owner_id == runtime_client.workspace.result.owner.owner_id
        assert (query, limit) == ("散步", 10)

        # A question is required, and an unbounded one is refused rather than
        # passed down to memory.
        assert (
            await client.get("/api/local/v1/recollections", headers=headers)
        ).status_code == 422
        assert (
            await client.get(
                "/api/local/v1/recollections",
                params={"q": "x", "limit": 500},
                headers=headers,
            )
        ).status_code == 422

        assert (
            await client.get("/api/local/v1/recollections", params={"q": "x"})
        ).status_code == 401


@pytest.mark.asyncio
async def test_an_owner_gives_their_eidolon_a_face_and_only_their_own(
    tmp_path: Path,
    short_runtime_dir: Path,
) -> None:
    """A face is bytes, and whose Eidolon it is decided in the same one place."""

    async with _local_api_session(tmp_path, short_runtime_dir) as (
        client,
        headers,
        runtime_client,
        _devices_client,
    ):
        companion_id = "c_11111111111111111111111111111111"
        path = f"/api/local/v1/companions/{companion_id}/face"
        jpeg = b"\xff\xd8\xff a face \xff\xd9"

        blank = await client.get(f"{path}-state", headers=headers)
        assert blank.status_code == 200
        assert blank.json()["has_face"] is False
        # The state answer never carries the photograph itself.
        assert "face" not in blank.json() or isinstance(blank.json().get("sha256"), type(None))
        assert (await client.get(path, headers=headers)).status_code == 204

        stored = await client.put(path, content=jpeg, headers=headers)
        assert stored.status_code == 200
        assert stored.json()["has_face"] is True
        assert stored.json()["sha256"] == hashlib.sha256(jpeg).hexdigest()

        served = await client.get(path, headers=headers)
        assert served.status_code == 200
        assert served.content == jpeg
        assert served.headers["content-type"] == "image/jpeg"

        cleared = await client.delete(path, headers=headers)
        assert cleared.status_code == 200
        assert cleared.json()["has_face"] is False
        assert (await client.get(path, headers=headers)).status_code == 204

        # The same session, somebody else's Eidolon: absent, not forbidden.
        runtime_client.owner_of_companion = "owner-somebody-else"
        assert (
            await client.put(path, content=jpeg, headers=headers)
        ).status_code == 404
        assert (await client.get(path, headers=headers)).status_code == 404
        assert (await client.delete(path, headers=headers)).status_code == 404
        assert (await client.get(f"{path}-state", headers=headers)).status_code == 404

        # And without a session at all.
        assert (await client.get(path)).status_code == 401
        assert (await client.put(path, content=jpeg)).status_code == 401


@pytest.mark.asyncio
async def test_a_person_renames_themselves_without_naming_themselves(
    tmp_path: Path,
    short_runtime_dir: Path,
) -> None:
    """Unlike a Companion, an Owner carries no identifier in the request.

    There is exactly one Owner a session can speak for. Taking an id here
    would invent a question — "is this your Owner?" — that the session has
    already answered, and every place a question is asked twice is a place it
    can be answered two ways.
    """

    async with _local_api_session(tmp_path, short_runtime_dir) as (
        client,
        headers,
        runtime_client,
        _devices_client,
    ):
        renamed = await client.patch(
            "/api/local/v1/owner",
            json={"contract_version": "1", "display_name": "  曼森  "},
            headers=headers,
        )

        assert renamed.status_code == 200
        assert renamed.json()["display_name"] == "曼森"
        owner_id, name = runtime_client.renamed_owner
        assert name == "曼森"
        # The Owner written to is the session's own, not one a client chose.
        assert owner_id == runtime_client.workspace.result.owner.owner_id

        runtime_client.renamed_owner = None
        blank = await client.patch(
            "/api/local/v1/owner",
            json={"contract_version": "1", "display_name": "   "},
            headers=headers,
        )
        assert blank.status_code == 422
        assert runtime_client.renamed_owner is None

        unauthenticated = await client.patch(
            "/api/local/v1/owner",
            json={"contract_version": "1", "display_name": "谁"},
        )
        assert unauthenticated.status_code == 401
        assert runtime_client.renamed_owner is None


@pytest.mark.asyncio
async def test_a_name_the_host_cannot_carry_out_is_refused_before_it_is_written(
    tmp_path: Path,
    short_runtime_dir: Path,
) -> None:
    async with _local_api_session(tmp_path, short_runtime_dir) as (
        client,
        headers,
        runtime_client,
        _devices_client,
    ):
        blank = await client.patch(
            "/api/local/v1/companions/c_11111111111111111111111111111111",
            json={"contract_version": "1", "display_name": "   "},
            headers=headers,
        )

        # Whitespace is a name that would erase the one they have, and it is
        # refused where the person is asking rather than two services away.
        assert blank.status_code == 422
        assert runtime_client.renamed is None

        # A name is taken as typed, minus the spaces around it.
        padded = await client.patch(
            "/api/local/v1/companions/c_11111111111111111111111111111111",
            json={"contract_version": "1", "display_name": "  小忆  "},
            headers=headers,
        )

        assert padded.status_code == 200
        assert padded.json()["display_name"] == "小忆"


@pytest.mark.asyncio
async def test_a_person_is_shown_what_their_eidolon_became_not_what_it_considered(
    tmp_path: Path,
    short_runtime_dir: Path,
) -> None:
    """Living with an Eidolon is not reviewing it.

    The authority stores proposals, because whatever changes a Companion needs
    somewhere to stage. Handing them to the person turns growth into a queue of
    approvals they have no basis to judge, so this boundary drops them: what is
    shown is what it has actually been.
    """

    async with _local_api_session(tmp_path, short_runtime_dir) as (
        client,
        headers,
        _runtime_client,
        _devices_client,
    ):
        history = await client.get(
            "/api/local/v1/companions/c_11111111111111111111111111111111/persona",
            headers=headers,
        )

        assert history.status_code == 200
        chapters = history.json()["chapters"]
        assert [chapter["chapter_id"] for chapter in chapters] == ["g_1"]
        # Nothing about hashes, versions or schemas reaches this view.
        assert set(chapters[0]) == {
            "chapter_id",
            "changed_at",
            "what_changed",
            "restored_from",
            "is_current",
        }
        # Nothing was recorded for the first chapter, and nothing is invented.
        assert chapters[0]["what_changed"] == ""


@pytest.mark.asyncio
async def test_going_back_answers_with_where_that_leaves_them(
    tmp_path: Path,
    short_runtime_dir: Path,
) -> None:
    async with _local_api_session(tmp_path, short_runtime_dir) as (
        client,
        headers,
        runtime_client,
        _devices_client,
    ):
        restored = await client.post(
            "/api/local/v1/companions/c_11111111111111111111111111111111"
            "/persona-restorations",
            json={"contract_version": "1", "chapter_id": "g_1"},
            headers=headers,
        )

        assert restored.status_code == 200
        assert runtime_client.restored is not None
        genome_id, summary = runtime_client.restored
        assert genome_id == "g_1"
        # Said in the Owner's voice, because the Owner is who did it.
        assert summary
        # The history comes back, not just the new chapter: what someone wants
        # to see after going back is where that leaves them.
        assert restored.json()["operation"] == "local.persona-history"


@pytest.mark.asyncio
async def test_another_owners_persona_is_not_readable_or_restorable(
    tmp_path: Path,
    short_runtime_dir: Path,
) -> None:
    async with _local_api_session(tmp_path, short_runtime_dir) as (
        client,
        headers,
        runtime_client,
        _devices_client,
    ):
        runtime_client.owner_of_companion = "owner-somebody-else"
        path = "/api/local/v1/companions/c_11111111111111111111111111111111"

        read = await client.get(f"{path}/persona", headers=headers)
        wrote = await client.post(
            f"{path}/persona-restorations",
            json={"contract_version": "1", "chapter_id": "g_1"},
            headers=headers,
        )

        assert read.status_code == 404
        assert wrote.status_code == 404
        assert runtime_client.restored is None


@pytest.mark.asyncio
async def test_a_directory_that_cannot_be_reached_costs_names_and_nothing_else(
    tmp_path: Path,
    short_runtime_dir: Path,
) -> None:
    """Two authorities answer here, and neither speaks for the other.

    Kernel decides what is this Owner's; Hub only says what those things are
    called. So a Hub that cannot be reached must not make someone's devices
    disappear — it costs the names, and an absent name stays absent rather
    than being replaced with an identifier.
    """

    async with _local_api_session(tmp_path, short_runtime_dir) as (
        client,
        headers,
        _runtime_client,
        devices_client,
    ):
        devices_client.directory = None

        devices = await client.get("/api/local/v1/devices", headers=headers)

        assert devices.status_code == 200
        listed = devices.json()["devices"]
        assert [device["device_id"] for device in listed] == ["device-local-1"]
        assert listed[0]["display_name"] == ""
        assert listed[0]["device_kind"] == ""


@pytest.mark.asyncio
async def test_an_owner_names_a_device_and_the_list_says_so(
    tmp_path: Path,
    short_runtime_dir: Path,
) -> None:
    """A device names itself when it enrols, and never gets to be renamed.

    An ESP32 reports its board, so two of the same one arrive as the same
    word. The Owner is the only one who knows which is in the living room.
    """

    async with _local_api_session(tmp_path, short_runtime_dir) as (
        client,
        headers,
        _runtime_client,
        devices_client,
    ):
        renamed = await client.patch(
            "/api/local/v1/devices/device-local-1",
            json={"contract_version": "1", "display_name": "  书房的 Box-3  "},
            headers=headers,
        )

        assert renamed.status_code == 200
        # Trimmed, because someone typing a name with a stray space meant the
        # name, and answered from the list so what is shown is what was kept.
        assert devices_client.renamed == ("device-local-1", "书房的 Box-3")
        assert renamed.json()["display_name"] == "书房的 Box-3"


@pytest.mark.asyncio
async def test_a_device_that_is_not_this_owners_cannot_be_renamed(
    tmp_path: Path,
    short_runtime_dir: Path,
) -> None:
    async with _local_api_session(tmp_path, short_runtime_dir) as (
        client,
        headers,
        _runtime_client,
        devices_client,
    ):
        refused = await client.patch(
            "/api/local/v1/devices/device-of-someone-else",
            json={"contract_version": "1", "display_name": "我的"},
            headers=headers,
        )
        blank = await client.patch(
            "/api/local/v1/devices/device-local-1",
            json={"contract_version": "1", "display_name": "   "},
            headers=headers,
        )

        assert refused.status_code == 404
        # Whitespace would erase the name they have, and is refused where the
        # person is asking rather than two services away.
        assert blank.status_code == 422
        assert devices_client.renamed is None
