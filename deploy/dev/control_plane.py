"""Prepare and validate an isolated local Eidolon OS control-plane runtime.

This module deliberately owns only development/runtime materialization.  It
never edits sibling repositories, never reads the formal System Data database,
and never starts a process.  ``deploy/dev/run_all.sh`` remains the process
lifecycle entrypoint after this preflight succeeds.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import secrets
import socket
import sqlite3
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import yaml

DATA_CONTRACT = "https://eidolon.dev/data/contracts/v1/companion/identity.schema.json"
DATA_RUNTIME_CONTRACT = (
    "https://eidolon.dev/data/contracts/v1/companion/runtime-snapshot.schema.json"
)
DATA_WORKSPACE_CONTRACT = (
    "https://eidolon.live/contracts/system-data/workspace/"
    "onboarding-operation-v1.schema.json"
)
HUB_CONTRACT = "eidolon.hub.device-directory.v1"
KERNEL_CONTRACT = "eidolon.kernel.device-mount.v1"

_EXPECTED_SERVICES = {
    "data": {
        "supervisord": "data:data-api",
        "endpoints": (
            {
                "endpoint_id": "companion-authority.http",
                "protocol": "http",
                "address": "http://127.0.0.1:8084",
                "contract": DATA_CONTRACT,
                "health_url": "http://127.0.0.1:8084/health",
            },
            {
                "endpoint_id": "companion-runtime-authority.http",
                "protocol": "http",
                "address": "http://127.0.0.1:8084",
                "contract": DATA_RUNTIME_CONTRACT,
                "health_url": "http://127.0.0.1:8084/health",
            },
        ),
    },
    "data-workspace": {
        "supervisord": "data:data-workspace-api",
        "endpoints": (
            {
                "endpoint_id": "workspace-authority.http",
                "protocol": "http",
                "address": "http://127.0.0.1:8085",
                "contract": DATA_WORKSPACE_CONTRACT,
                "health_url": "http://127.0.0.1:8085/health",
            },
        ),
    },
    "hub": {
        "supervisord": "hub:hub-api",
        "endpoints": (
            {
                "endpoint_id": "device-authority.http",
                "protocol": "http",
                "address": "http://127.0.0.1:8082",
                "contract": HUB_CONTRACT,
                "health_url": "http://127.0.0.1:8082/health",
            },
        ),
    },
    "kernel": {
        "supervisord": "kernel:kernel-api",
        "endpoints": (
            {
                "endpoint_id": "device-mount.http",
                "protocol": "http",
                "address": "http://127.0.0.1:8083",
                "contract": KERNEL_CONTRACT,
                "health_url": "http://127.0.0.1:8083/health",
            },
        ),
    },
}

_DATA_TABLES = {
    "alembic_version",
    "audit_outbox",
    "companion_face_assets",
    "companions",
    "guard_bindings",
    "memory_realms",
    "owner_face_profile_revisions",
    "owner_face_references",
    "owners",
    "persona_genomes",
}


class ControlPlanePreparationError(RuntimeError):
    """The isolated runtime cannot be safely prepared or consumed."""


@dataclass(frozen=True, slots=True)
class RuntimeLayout:
    admin_root: Path
    eidolon_root: Path
    runtime_root: Path

    @property
    def env_dir(self) -> Path:
        return self.runtime_root / "env"

    @property
    def config_dir(self) -> Path:
        return self.runtime_root / "config"

    @property
    def data_dir(self) -> Path:
        return self.runtime_root / "data"

    @property
    def data_database(self) -> Path:
        return self.data_dir / "eidolon-system.sqlite3"

    @property
    def hub_database(self) -> Path:
        return self.data_dir / "eidolon-hub.sqlite3"

    @property
    def kernel_database(self) -> Path:
        return self.data_dir / "eidolon-kernel.sqlite3"

    @property
    def system_database(self) -> Path:
        return self.data_dir / "eidolond.sqlite3"

    @property
    def system_socket(self) -> Path:
        return self.runtime_root / "eidolond.sock"

    @property
    def supervisor_config(self) -> Path:
        return self.admin_root / "deploy/dev/supervisord.profile.conf"

    @property
    def manifest(self) -> Path:
        return self.eidolon_root / "eidolon_kernel/config/system-services.yaml"


def default_layout() -> RuntimeLayout:
    from eidolon_admin_server.app.settings import default_eidolon_root

    admin_root = Path(__file__).resolve().parents[2]
    runtime_override = os.environ.get("EIDOLON_ADMIN_CONTROL_PLANE_ROOT", "").strip()
    runtime_root = (
        Path(runtime_override).expanduser().resolve()
        if runtime_override
        else admin_root / "var/os-control-plane"
    )
    return RuntimeLayout(
        admin_root=admin_root,
        eidolon_root=default_eidolon_root(),
        runtime_root=runtime_root,
    )


def prepare(layout: RuntimeLayout, *, migrate: bool = True) -> None:
    """Materialize secrets/configs and optionally migrate the isolated Data DB."""

    _validate_layout_boundary(layout)
    for directory in (
        layout.runtime_root,
        layout.env_dir,
        layout.config_dir,
        layout.data_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
    _clear_stale_system_socket(layout.system_socket)

    existing = _read_existing_secrets(layout)
    data_token = existing.get("data_token") or secrets.token_urlsafe(32)
    workspace_token = existing.get("workspace_token") or secrets.token_urlsafe(32)
    local_api_token = existing.get("local_api_token") or secrets.token_urlsafe(32)
    hub_reader_token = existing.get("hub_reader_token") or secrets.token_urlsafe(32)
    hub_jwt_secret = existing.get("hub_jwt_secret") or secrets.token_urlsafe(48)
    hub_provider_token = existing.get("hub_provider_token") or secrets.token_urlsafe(32)

    env_documents = {
        "data.env": {
            "EIDOLON_DATA_COMPANION_AUTHORITY_TOKEN": data_token,
            "EIDOLON_DATA_WORKSPACE_AUTHORITY_TOKEN": workspace_token,
            "EIDOLON_DATA_SQLITE_PATH": str(layout.data_database),
            "EIDOLON_DATA_DATABASE_URL": (
                f"sqlite+aiosqlite:///{layout.data_database}"
            ),
            "EIDOLON_DATA_OBJECT_STORE_PATH": str(layout.data_dir / "objects"),
        },
        "hub.env": {
            "EIDOLON_HUB_MANAGEMENT_JWT_SECRET": hub_jwt_secret,
            "EIDOLON_HUB_DEVICE_REGISTRY_READER_TOKEN": hub_reader_token,
            "EIDOLON_HUB_CHANNEL_PROVIDER_TOKEN": hub_provider_token,
            "EIDOLON_HUB_SETTINGS_YAML": str(layout.config_dir / "hub.yaml"),
        },
        "kernel.env": {
            "EIDOLON_KERNEL_HUB_MANAGEMENT_TOKEN": hub_reader_token,
            "EIDOLON_KERNEL_COMPANION_AUTHORITY_TOKEN": data_token,
            "EIDOLON_KERNEL_SETTINGS_YAML": str(layout.config_dir / "kernel.yaml"),
        },
        "admin.env": {
            "EIDOLON_ADMIN_DATA_AUTHORITY_TOKEN": data_token,
            "EIDOLON_ADMIN_DATA_WORKSPACE_AUTHORITY_TOKEN": workspace_token,
            "EIDOLON_ADMIN_LOCAL_API_SERVICE_TOKEN": local_api_token,
            "EIDOLON_ADMIN_SYSTEM_DIRECTORY_URL": "http://127.0.0.1:8090",
            "EIDOLON_ADMIN_SYSTEM_DIRECTORY_UDS": str(layout.system_socket),
        },
        "local-api.env": {
            "EIDOLON_LOCAL_API_ADMIN_BASE_URL": "http://127.0.0.1:9000",
            "EIDOLON_LOCAL_API_ADMIN_SERVICE_TOKEN": local_api_token,
        },
        "eidolond.env": {
            "EIDOLON_SYSTEM_SETTINGS_YAML": str(layout.config_dir / "eidolond.yaml"),
        },
    }
    for name, values in env_documents.items():
        _atomic_write_env(layout.env_dir / name, values)

    config_documents = {
        "hub.yaml": {
            "onboarding": {
                "hub_id": "eidolon-hub-control-plane-sandbox",
                "public_base_url": "https://eidolon-hub.local",
                "retrieval_window_seconds": 1800,
            },
            "discovery": {"mdns": {"enabled": False}},
            "channel_provider": {"contract_url": "http://127.0.0.1:8091/v1"},
            "persistence": {"path": str(layout.hub_database)},
        },
        "kernel.yaml": {
            "persistence": {"path": str(layout.kernel_database)},
            "system_directory": {
                "base_url": "http://eidolond",
                "uds_path": str(layout.system_socket),
                "timeout_seconds": 2.0,
            },
            "hub": {"timeout_seconds": 3.0},
            "companion_authority": {"timeout_seconds": 3.0},
            "reconciliation": {"interval_seconds": 30.0},
            "deployment": {"mode": "trusted-local", "trusted_local_ingress": True},
        },
        "eidolond.yaml": {
            "manifest": {"path": str(layout.manifest)},
            "persistence": {"path": str(layout.system_database)},
            "host": {
                "driver": "supervisord",
                "supervisorctl": str(layout.admin_root / ".venv/bin/supervisorctl"),
                "supervisor_config": str(layout.supervisor_config),
                "command_timeout_seconds": 20,
            },
            "reconciliation": {
                "interval_seconds": 2,
                "readiness_timeout_seconds": 3,
            },
            "interface": {
                "host": "127.0.0.1",
                "port": 8090,
                "uds": str(layout.system_socket),
                "uds_mode": "0600",
            },
        },
    }
    for name, document in config_documents.items():
        _atomic_write_yaml(layout.config_dir / name, document)

    if migrate:
        migrate_data(layout)
    validate(layout, require_database=migrate)


def migrate_data(layout: RuntimeLayout) -> None:
    """Apply only the tracked Data V2 migration to the isolated database."""

    _validate_layout_boundary(layout)
    data_root = layout.eidolon_root / "eidolon_data"
    alembic = data_root / ".venv/bin/alembic"
    if not alembic.is_file():
        raise ControlPlanePreparationError(
            f"Data Alembic entrypoint is missing: {alembic}"
        )
    environment = os.environ.copy()
    environment.update(_parse_env(layout.env_dir / "data.env"))
    result = subprocess.run(
        (str(alembic), "-c", str(data_root / "alembic.ini"), "upgrade", "head"),
        cwd=data_root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        detail = (
            result.stderr.strip() or result.stdout.strip() or "no diagnostic output"
        )
        raise ControlPlanePreparationError(f"isolated Data migration failed: {detail}")
    layout.data_database.chmod(0o600)
    _checkpoint_isolated_database(layout.data_database)


def validate(layout: RuntimeLayout, *, require_database: bool = True) -> None:
    """Fail closed on contract drift, secret mismatch, or a non-isolated path."""

    _validate_layout_boundary(layout)
    _validate_manifest(layout.manifest)
    _validate_runtime_files(layout)
    _validate_entrypoints(layout)
    if require_database:
        _validate_data_database(layout.data_database)


def issue_operator_token(
    layout: RuntimeLayout,
    *,
    subject: str = "eidolon-local-operator",
    ttl_seconds: int = 3600,
) -> Path:
    """Write a short-lived sandbox Hub admin JWT without printing its secret."""

    validate(layout)
    if not subject.strip() or len(subject) > 255:
        raise ControlPlanePreparationError("operator token subject is invalid")
    if ttl_seconds < 60 or ttl_seconds > 86_400:
        raise ControlPlanePreparationError(
            "operator token TTL must be between 60 and 86400 seconds"
        )
    hub = _parse_env(layout.env_dir / "hub.env")
    secret = hub["EIDOLON_HUB_MANAGEMENT_JWT_SECRET"].encode()
    issued_at = int(time.time())
    header = _base64url_json({"alg": "HS256", "typ": "JWT"})
    payload = _base64url_json(
        {
            "aud": "eidolon-hub",
            "exp": issued_at + ttl_seconds,
            "iat": issued_at,
            "roles": ["hub-admin"],
            "sub": subject.strip(),
        }
    )
    signing_input = header + b"." + payload
    signature = base64.urlsafe_b64encode(
        hmac.new(secret, signing_input, hashlib.sha256).digest()
    ).rstrip(b"=")
    destination = layout.runtime_root / "operator.jwt"
    _atomic_write(
        destination, (signing_input + b"." + signature).decode() + "\n", mode=0o600
    )
    return destination


def validate_supervisor_config(layout: RuntimeLayout) -> None:
    """Parse the composed Supervisor config without opening sockets or logs."""

    try:
        from supervisor.options import ServerOptions

        options = ServerOptions()
        options.realize(args=("-c", str(layout.supervisor_config)))
    except (OSError, ValueError, SystemExit) as exc:
        raise ControlPlanePreparationError(
            f"supervisor profile configuration is invalid: {layout.supervisor_config}"
        ) from exc


def _validate_layout_boundary(layout: RuntimeLayout) -> None:
    runtime = layout.runtime_root.resolve()
    admin = layout.admin_root.resolve()
    formal = (Path.home() / "eidolon/data").resolve()
    if runtime == admin or admin not in runtime.parents:
        raise ControlPlanePreparationError(
            f"control-plane runtime must stay below the Admin worktree: {runtime}"
        )
    if runtime == formal or formal in runtime.parents:
        raise ControlPlanePreparationError(
            "control-plane runtime must not use the formal data root"
        )


def _clear_stale_system_socket(path: Path) -> None:
    if not path.exists():
        return
    if not path.is_socket():
        raise ControlPlanePreparationError(
            f"eidolond UDS path exists but is not a socket: {path}"
        )
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(0.2)
    try:
        client.connect(str(path))
    except (ConnectionRefusedError, FileNotFoundError):
        path.unlink(missing_ok=True)
    except OSError as exc:
        raise ControlPlanePreparationError(
            f"cannot prove eidolond UDS is stale: {path}: {exc}"
        ) from exc
    else:
        raise ControlPlanePreparationError(
            f"isolated eidolond is already accepting connections: {path}"
        )
    finally:
        client.close()


def _checkpoint_isolated_database(path: Path) -> None:
    connection = sqlite3.connect(path, timeout=5.0)
    try:
        checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    finally:
        connection.close()
    if checkpoint is not None and int(checkpoint[0]) != 0:
        raise ControlPlanePreparationError(
            f"isolated Data database is busy during checkpoint: {path}"
        )


def _validate_manifest(path: Path) -> None:
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        raise ControlPlanePreparationError(
            f"system service manifest is unavailable: {path}"
        ) from exc
    services = document.get("services") if isinstance(document, dict) else None
    if (
        not isinstance(document, dict)
        or document.get("version") != 1
        or not isinstance(services, list)
    ):
        raise ControlPlanePreparationError("system service manifest must be version 1")
    by_id = {
        item.get("service_id"): item
        for item in services
        if isinstance(item, dict) and isinstance(item.get("service_id"), str)
    }
    required_enabled = {
        item["service_id"]
        for item in services
        if isinstance(item, dict)
        and isinstance(item.get("service_id"), str)
        and item.get("required") is True
        and item.get("enabled_by_default") is True
    }
    expected_services = set(_EXPECTED_SERVICES)
    if required_enabled != expected_services:
        missing = sorted(expected_services - required_enabled)
        unwired = sorted(required_enabled - expected_services)
        raise ControlPlanePreparationError(
            f"required manifest service set drift: missing={missing}, unwired={unwired}"
        )
    for service_id, expected in _EXPECTED_SERVICES.items():
        service = by_id.get(service_id)
        if not isinstance(service, dict):
            raise ControlPlanePreparationError(
                f"manifest does not publish service {service_id}"
            )
        targets = service.get("host_targets")
        if (
            not isinstance(targets, dict)
            or targets.get("supervisord") != expected["supervisord"]
        ):
            raise ControlPlanePreparationError(
                f"manifest supervisord target drift for {service_id}"
            )
        endpoints = service.get("endpoints")
        expected_endpoints = expected["endpoints"]
        if not isinstance(endpoints, list) or len(endpoints) != len(expected_endpoints):
            raise ControlPlanePreparationError(
                f"manifest endpoint set drift for {service_id}"
            )
        by_endpoint_id = {
            endpoint.get("endpoint_id"): endpoint
            for endpoint in endpoints
            if isinstance(endpoint, dict)
        }
        for expected_endpoint in expected_endpoints:
            endpoint_id = expected_endpoint["endpoint_id"]
            endpoint = by_endpoint_id.get(endpoint_id)
            for key in (
                "endpoint_id",
                "protocol",
                "address",
                "contract",
                "health_url",
            ):
                if (
                    not isinstance(endpoint, dict)
                    or endpoint.get(key) != expected_endpoint[key]
                ):
                    raise ControlPlanePreparationError(
                        f"manifest {service_id}/{endpoint_id}.{key} does not "
                        "match the consumed contract"
                    )


def _validate_runtime_files(layout: RuntimeLayout) -> None:
    data = _parse_env(layout.env_dir / "data.env")
    hub = _parse_env(layout.env_dir / "hub.env")
    kernel = _parse_env(layout.env_dir / "kernel.env")
    admin = _parse_env(layout.env_dir / "admin.env")
    local_api = _parse_env(layout.env_dir / "local-api.env")
    if len(data.get("EIDOLON_DATA_COMPANION_AUTHORITY_TOKEN", "")) < 24:
        raise ControlPlanePreparationError("Data companion authority token is invalid")
    if len(data.get("EIDOLON_DATA_WORKSPACE_AUTHORITY_TOKEN", "")) < 24:
        raise ControlPlanePreparationError("Data workspace authority token is invalid")
    if len(hub.get("EIDOLON_HUB_DEVICE_REGISTRY_READER_TOKEN", "").encode()) < 32:
        raise ControlPlanePreparationError("Hub reader token is invalid")
    if len(hub.get("EIDOLON_HUB_MANAGEMENT_JWT_SECRET", "").encode()) < 32:
        raise ControlPlanePreparationError("Hub management JWT secret is invalid")
    if len(hub.get("EIDOLON_HUB_CHANNEL_PROVIDER_TOKEN", "").encode()) < 24:
        raise ControlPlanePreparationError("Hub channel provider token is invalid")
    data_token = data["EIDOLON_DATA_COMPANION_AUTHORITY_TOKEN"]
    if kernel.get("EIDOLON_KERNEL_COMPANION_AUTHORITY_TOKEN") != data_token:
        raise ControlPlanePreparationError("Kernel/Data companion credential mismatch")
    if admin.get("EIDOLON_ADMIN_DATA_AUTHORITY_TOKEN") != data_token:
        raise ControlPlanePreparationError("Admin/Data companion credential mismatch")
    workspace_token = data["EIDOLON_DATA_WORKSPACE_AUTHORITY_TOKEN"]
    if admin.get("EIDOLON_ADMIN_DATA_WORKSPACE_AUTHORITY_TOKEN") != workspace_token:
        raise ControlPlanePreparationError("Admin/Data workspace credential mismatch")
    local_api_token = admin.get("EIDOLON_ADMIN_LOCAL_API_SERVICE_TOKEN", "")
    if len(local_api_token) < 24:
        raise ControlPlanePreparationError("Admin Local API credential is invalid")
    if local_api.get("EIDOLON_LOCAL_API_ADMIN_SERVICE_TOKEN") != local_api_token:
        raise ControlPlanePreparationError("Local API/Admin credential mismatch")
    if (
        kernel.get("EIDOLON_KERNEL_HUB_MANAGEMENT_TOKEN")
        != hub["EIDOLON_HUB_DEVICE_REGISTRY_READER_TOKEN"]
    ):
        raise ControlPlanePreparationError("Kernel/Hub reader credential mismatch")
    expected_data_path = str(layout.data_database)
    if data.get("EIDOLON_DATA_SQLITE_PATH") != expected_data_path:
        raise ControlPlanePreparationError("Data path escaped the isolated runtime")
    for name in ("hub.yaml", "kernel.yaml", "eidolond.yaml"):
        path = layout.config_dir / name
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ControlPlanePreparationError(
                f"runtime config is unavailable: {path}"
            ) from exc
        if not isinstance(document, dict):
            raise ControlPlanePreparationError(
                f"runtime config must be a mapping: {path}"
            )


def _validate_entrypoints(layout: RuntimeLayout) -> None:
    expected = (
        layout.admin_root / ".venv/bin/uvicorn",
        layout.admin_root / ".venv/bin/supervisorctl",
        layout.eidolon_root / "eidolon_data/.venv/bin/uvicorn",
        layout.eidolon_root / "eidolon_hub/.venv/bin/uvicorn",
        layout.eidolon_root / "eidolon_kernel/.venv/bin/uvicorn",
        layout.eidolon_root / "eidolon_kernel/.venv/bin/eidolond",
    )
    missing = [
        str(path)
        for path in expected
        if not path.is_file() or not os.access(path, os.X_OK)
    ]
    if missing:
        raise ControlPlanePreparationError(
            "runtime entrypoints are missing or not executable: " + ", ".join(missing)
        )


def _validate_data_database(path: Path) -> None:
    if not path.is_file():
        raise ControlPlanePreparationError(f"isolated Data database is missing: {path}")
    connection = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
    try:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            if not str(row[0]).startswith("sqlite_")
        }
        if tables != _DATA_TABLES:
            raise ControlPlanePreparationError(
                f"isolated Data table set drift: expected {sorted(_DATA_TABLES)}, got {sorted(tables)}"
            )
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if integrity != ("ok",):
            raise ControlPlanePreparationError(
                f"isolated Data integrity check failed: {integrity}"
            )
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise ControlPlanePreparationError(
                f"isolated Data foreign-key violations: {len(violations)}"
            )
        version = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
        if version != ("0001_system_data_v2",):
            raise ControlPlanePreparationError(
                f"isolated Data migration drift: {version}"
            )
    finally:
        connection.close()


def _read_existing_secrets(layout: RuntimeLayout) -> dict[str, str]:
    values: dict[str, str] = {}
    mappings = {
        "data_token": ("data.env", "EIDOLON_DATA_COMPANION_AUTHORITY_TOKEN"),
        "workspace_token": (
            "data.env",
            "EIDOLON_DATA_WORKSPACE_AUTHORITY_TOKEN",
        ),
        "local_api_token": (
            "admin.env",
            "EIDOLON_ADMIN_LOCAL_API_SERVICE_TOKEN",
        ),
        "hub_reader_token": ("hub.env", "EIDOLON_HUB_DEVICE_REGISTRY_READER_TOKEN"),
        "hub_jwt_secret": ("hub.env", "EIDOLON_HUB_MANAGEMENT_JWT_SECRET"),
        "hub_provider_token": ("hub.env", "EIDOLON_HUB_CHANNEL_PROVIDER_TOKEN"),
    }
    for destination, (filename, key) in mappings.items():
        path = layout.env_dir / filename
        if not path.exists():
            continue
        parsed = _parse_env(path)
        if key not in parsed:
            continue
        value = parsed[key]
        if not value:
            raise ControlPlanePreparationError(
                f"existing runtime secret is blank: {filename}:{key}"
            )
        values[destination] = value
    return values


def _parse_env(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ControlPlanePreparationError(
            f"runtime env file is unavailable: {path}"
        ) from exc
    result: dict[str, str] = {}
    for line in lines:
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        key, separator, value = text.partition("=")
        if not separator or not key.strip():
            raise ControlPlanePreparationError(
                f"invalid runtime env entry in {path.name}"
            )
        result[key.strip()] = value.strip()
    return result


def _atomic_write_env(path: Path, values: Mapping[str, str]) -> None:
    payload = "".join(f"{key}={value}\n" for key, value in values.items())
    _atomic_write(path, payload, mode=0o600)


def _atomic_write_yaml(path: Path, document: Mapping[str, object]) -> None:
    payload = yaml.safe_dump(document, sort_keys=False, allow_unicode=True)
    _atomic_write(path, payload, mode=0o600)


def _atomic_write(path: Path, payload: str, *, mode: int) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _base64url_json(document: Mapping[str, object]) -> bytes:
    payload = json.dumps(document, separators=(",", ":"), sort_keys=True).encode()
    return base64.urlsafe_b64encode(payload).rstrip(b"=")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="eidolon-admin-dev-control-plane",
        description="Prepare or validate the isolated local OS control-plane runtime.",
    )
    parser.add_argument(
        "operation",
        choices=(
            "prepare",
            "validate",
            "validate-supervisor",
            "migrate-data",
            "issue-operator-token",
        ),
    )
    parser.add_argument(
        "--without-migration",
        action="store_true",
        help="materialize runtime files without creating the isolated Data database",
    )
    parser.add_argument("--subject", default="eidolon-local-operator")
    parser.add_argument("--ttl-seconds", type=int, default=3600)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    layout = default_layout()
    try:
        if arguments.operation == "prepare":
            prepare(layout, migrate=not arguments.without_migration)
        elif arguments.operation == "validate":
            validate(layout, require_database=not arguments.without_migration)
        elif arguments.operation == "validate-supervisor":
            validate_supervisor_config(layout)
        elif arguments.operation == "migrate-data":
            migrate_data(layout)
            validate(layout)
        else:
            destination = issue_operator_token(
                layout,
                subject=arguments.subject,
                ttl_seconds=arguments.ttl_seconds,
            )
            print(f"sandbox operator token written with mode 0600: {destination}")
    except (ControlPlanePreparationError, OSError, subprocess.SubprocessError) as exc:
        print(f"control-plane preflight failed: {exc}", file=sys.stderr)
        return 1
    print(f"control-plane {arguments.operation} passed: {layout.runtime_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
