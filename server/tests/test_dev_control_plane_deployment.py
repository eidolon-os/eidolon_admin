from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import socket
import sqlite3
import tempfile
from pathlib import Path

import pytest
import yaml

from deploy.dev.control_plane import (
    DATA_CONTRACT,
    DATA_MEMORY_ROSTER_CONTRACT,
    DATA_RUNTIME_CONTRACT,
    DATA_WORKSPACE_CONTRACT,
    HUB_CONTRACT,
    KERNEL_CONTRACT,
    ControlPlanePreparationError,
    RuntimeLayout,
    issue_operator_token,
    migrate_data,
    prepare,
    validate,
    validate_supervisor_config,
)


def _manifest() -> dict[str, object]:
    return {
        "version": 1,
        "services": [
            {
                "service_id": "data",
                "required": True,
                "enabled_by_default": True,
                "dependencies": [],
                "host_targets": {"supervisord": "data:data-api"},
                "endpoints": [
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
                    {
                        "endpoint_id": "memory-runtime-roster.http",
                        "protocol": "http",
                        "address": "http://127.0.0.1:8084",
                        "contract": DATA_MEMORY_ROSTER_CONTRACT,
                        "health_url": "http://127.0.0.1:8084/health",
                    },
                ],
            },
            {
                "service_id": "data-workspace",
                "required": True,
                "enabled_by_default": True,
                "dependencies": ["data"],
                "host_targets": {"supervisord": "data:data-workspace-api"},
                "endpoints": [
                    {
                        "endpoint_id": "workspace-authority.http",
                        "protocol": "http",
                        "address": "http://127.0.0.1:8085",
                        "contract": DATA_WORKSPACE_CONTRACT,
                        "health_url": "http://127.0.0.1:8085/health",
                    }
                ],
            },
            {
                "service_id": "hub",
                "required": True,
                "enabled_by_default": True,
                "dependencies": [],
                "host_targets": {"supervisord": "hub:hub-api"},
                "endpoints": [
                    {
                        "endpoint_id": "device-authority.http",
                        "protocol": "http",
                        "address": "http://127.0.0.1:8082",
                        "contract": HUB_CONTRACT,
                        "health_url": "http://127.0.0.1:8082/health",
                    }
                ],
            },
            {
                "service_id": "kernel",
                "required": True,
                "enabled_by_default": True,
                "dependencies": [],
                "host_targets": {"supervisord": "kernel:kernel-api"},
                "endpoints": [
                    {
                        "endpoint_id": "device-mount.http",
                        "protocol": "http",
                        "address": "http://127.0.0.1:8083",
                        "contract": KERNEL_CONTRACT,
                        "health_url": "http://127.0.0.1:8083/health",
                    }
                ],
            },
        ],
    }


def _make_executable(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)


def _layout(tmp_path: Path) -> RuntimeLayout:
    admin = tmp_path / "admin"
    root = tmp_path / "eidolon"
    runtime = admin / "var/os-control-plane"
    runtime.mkdir(parents=True)
    manifest = root / "eidolon_kernel/config/system-services.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(yaml.safe_dump(_manifest(), sort_keys=False), encoding="utf-8")
    for path in (
        admin / ".venv/bin/uvicorn",
        admin / ".venv/bin/supervisorctl",
        root / "eidolon_data/.venv/bin/uvicorn",
        root / "eidolon_hub/.venv/bin/uvicorn",
        root / "eidolon_kernel/.venv/bin/uvicorn",
        root / "eidolon_kernel/.venv/bin/eidolond",
    ):
        _make_executable(path)
    supervisor = admin / "deploy/dev/supervisord.profile.conf"
    supervisor.parent.mkdir(parents=True)
    supervisor.write_text(
        "\n".join(
            (
                "[unix_http_server]",
                f"file={runtime / 'supervisor.sock'}",
                "",
                "[supervisord]",
                f"logfile={runtime / 'supervisord.log'}",
                f"pidfile={runtime / 'supervisord.pid'}",
                f"childlogdir={runtime}",
                "",
                "[rpcinterface:supervisor]",
                "supervisor.rpcinterface_factory = supervisor.rpcinterface:make_main_rpcinterface",
                "",
                "[supervisorctl]",
                f"serverurl=unix://{runtime / 'supervisor.sock'}",
                "",
            )
        ),
        encoding="utf-8",
    )
    return RuntimeLayout(admin_root=admin, eidolon_root=root, runtime_root=runtime)


def _read_env(path: Path) -> dict[str, str]:
    return dict(
        line.split("=", 1)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    )


@pytest.mark.unit
def test_prepare_materializes_isolated_config_and_preserves_secrets(
    tmp_path: Path,
) -> None:
    layout = _layout(tmp_path)

    prepare(layout, migrate=False)
    first_data = _read_env(layout.env_dir / "data.env")
    first_hub = _read_env(layout.env_dir / "hub.env")
    first_kernel = _read_env(layout.env_dir / "kernel.env")
    first_admin = _read_env(layout.env_dir / "admin.env")
    first_local_api = _read_env(layout.env_dir / "local-api.env")
    assert first_data["EIDOLON_DATA_SQLITE_PATH"] == str(layout.data_database)
    assert len(first_data["EIDOLON_DATA_MEMORY_RUNTIME_ROSTER_TOKEN"]) >= 24
    assert (
        str(Path.home() / "eidolon/data") not in first_data["EIDOLON_DATA_SQLITE_PATH"]
    )
    assert (
        first_data["EIDOLON_DATA_COMPANION_AUTHORITY_TOKEN"]
        == first_kernel["EIDOLON_KERNEL_COMPANION_AUTHORITY_TOKEN"]
        == first_admin["EIDOLON_ADMIN_DATA_AUTHORITY_TOKEN"]
    )
    assert (
        first_hub["EIDOLON_HUB_DEVICE_REGISTRY_READER_TOKEN"]
        == first_kernel["EIDOLON_KERNEL_HUB_MANAGEMENT_TOKEN"]
    )
    assert (
        first_data["EIDOLON_DATA_WORKSPACE_AUTHORITY_TOKEN"]
        == first_admin["EIDOLON_ADMIN_DATA_WORKSPACE_AUTHORITY_TOKEN"]
    )
    assert (
        first_admin["EIDOLON_ADMIN_LOCAL_API_SERVICE_TOKEN"]
        == first_local_api["EIDOLON_LOCAL_API_ADMIN_SERVICE_TOKEN"]
    )
    assert (layout.env_dir / "data.env").stat().st_mode & 0o777 == 0o600

    prepare(layout, migrate=False)
    assert _read_env(layout.env_dir / "data.env") == first_data
    assert _read_env(layout.env_dir / "hub.env") == first_hub
    assert _read_env(layout.env_dir / "admin.env") == first_admin
    assert _read_env(layout.env_dir / "local-api.env") == first_local_api


@pytest.mark.unit
def test_prepare_removes_only_a_stale_eidolond_socket() -> None:
    with tempfile.TemporaryDirectory(prefix="eacp-", dir="/tmp") as temporary:
        layout = _layout(Path(temporary))
        stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        stale.bind(str(layout.system_socket))
        stale.close()

        prepare(layout, migrate=False)

        assert not layout.system_socket.exists()


@pytest.mark.unit
def test_prepare_refuses_a_live_eidolond_socket() -> None:
    with tempfile.TemporaryDirectory(prefix="eacp-", dir="/tmp") as temporary:
        layout = _layout(Path(temporary))
        live = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        live.bind(str(layout.system_socket))
        live.listen(1)
        try:
            with pytest.raises(ControlPlanePreparationError, match="already accepting"):
                prepare(layout, migrate=False)
        finally:
            live.close()
            layout.system_socket.unlink(missing_ok=True)


@pytest.mark.unit
def test_validation_rejects_manifest_contract_drift(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    prepare(layout, migrate=False)
    document = _manifest()
    document["services"][0]["endpoints"][0]["contract"] = "legacy.contract"  # type: ignore[index]
    layout.manifest.write_text(
        yaml.safe_dump(document, sort_keys=False), encoding="utf-8"
    )

    with pytest.raises(
        ControlPlanePreparationError,
        match=r"data/companion-authority\.http\.contract",
    ):
        validate(layout, require_database=False)


@pytest.mark.unit
def test_validation_rejects_runtime_outside_admin_boundary(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    unsafe = RuntimeLayout(
        admin_root=layout.admin_root,
        eidolon_root=layout.eidolon_root,
        runtime_root=tmp_path / "outside",
    )
    with pytest.raises(ControlPlanePreparationError, match="below the Admin worktree"):
        prepare(unsafe, migrate=False)


@pytest.mark.unit
def test_supervisor_validation_only_parses_configuration(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    validate_supervisor_config(layout)
    assert not (layout.runtime_root / "supervisor.sock").exists()
    assert not (layout.runtime_root / "supervisord.pid").exists()

    layout.supervisor_config.write_text("[supervisord\n", encoding="utf-8")
    with pytest.raises(ControlPlanePreparationError, match="configuration is invalid"):
        validate_supervisor_config(layout)


@pytest.mark.unit
def test_operator_token_is_short_lived_signed_and_not_printed(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    prepare(layout, migrate=False)
    # Token issuance requires a validated Data DB; create the exact minimal
    # schema here so this remains a pure unit test.
    connection = sqlite3.connect(layout.data_database)
    try:
        for table in (
            "audit_outbox",
            "companion_face_assets",
            "companions",
            "guard_bindings",
            "memory_realms",
            "owner_face_profile_revisions",
            "owner_face_references",
            "owners",
            "persona_genomes",
        ):
            connection.execute(f'CREATE TABLE "{table}" (id INTEGER)')
        connection.execute("CREATE TABLE alembic_version (version_num TEXT NOT NULL)")
        connection.execute("INSERT INTO alembic_version VALUES ('0001_system_data_v2')")
        connection.commit()
    finally:
        connection.close()

    destination = issue_operator_token(layout, subject="test-operator", ttl_seconds=300)
    token = destination.read_text(encoding="utf-8").strip()
    header, payload, signature = token.split(".")
    claims = json.loads(base64.urlsafe_b64decode(payload + "=="))
    secret = _read_env(layout.env_dir / "hub.env")[
        "EIDOLON_HUB_MANAGEMENT_JWT_SECRET"
    ].encode()
    expected = base64.urlsafe_b64encode(
        hmac.new(secret, f"{header}.{payload}".encode(), hashlib.sha256).digest()
    ).rstrip(b"=")
    assert hmac.compare_digest(signature.encode(), expected)
    assert claims["aud"] == "eidolon-hub"
    assert claims["sub"] == "test-operator"
    assert claims["roles"] == ["hub-admin"]
    assert 0 < claims["exp"] - claims["iat"] <= 300
    assert destination.stat().st_mode & 0o777 == 0o600


@pytest.mark.contract
def test_supervisor_targets_match_the_published_manifest() -> None:
    admin_root = Path(__file__).resolve().parents[2]
    eidolon_root = admin_root.parent
    if admin_root.joinpath(".git").is_file():
        from eidolon_admin_server.app.settings import default_eidolon_root

        eidolon_root = default_eidolon_root()
    manifest = yaml.safe_load(
        eidolon_root.joinpath("eidolon_kernel/config/system-services.yaml").read_text(
            encoding="utf-8"
        )
    )
    targets = {
        item["service_id"]: item["host_targets"]["supervisord"]
        for item in manifest["services"]
    }
    assert targets == {
        "data": "data:data-api",
        "data-workspace": "data:data-workspace-api",
        "hub": "hub:hub-api",
        "kernel": "kernel:kernel-api",
    }
    for filename, target in (
        ("data.conf", "[program:data-api]"),
        ("data.conf", "[program:data-workspace-api]"),
        ("hub-os-control-plane.conf", "[program:hub-api]"),
        ("kernel.conf", "[program:kernel-api]"),
    ):
        text = admin_root.joinpath("deploy/supervisor/available", filename).read_text(
            encoding="utf-8"
        )
        assert target in text
        assert "autostart=false" in text


@pytest.mark.integration
def test_real_data_migration_targets_only_the_isolated_database(tmp_path: Path) -> None:
    from eidolon_admin_server.app.settings import default_eidolon_root

    real_root = default_eidolon_root()
    layout = _layout(tmp_path)
    layout = RuntimeLayout(
        admin_root=layout.admin_root,
        eidolon_root=real_root,
        runtime_root=layout.runtime_root,
    )
    # Keep fake Admin entrypoints but use the real sibling Data migration and
    # real current manifest. No process is started.
    prepare(layout, migrate=False)
    migrate_data(layout)
    validate(layout)
    connection = sqlite3.connect(f"file:{layout.data_database}?mode=ro", uri=True)
    try:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    finally:
        connection.close()
    assert os.path.commonpath((layout.data_database, layout.runtime_root)) == str(
        layout.runtime_root
    )
    assert not layout.data_database.with_name(
        f"{layout.data_database.name}-wal"
    ).exists()
    assert not layout.data_database.with_name(
        f"{layout.data_database.name}-shm"
    ).exists()
