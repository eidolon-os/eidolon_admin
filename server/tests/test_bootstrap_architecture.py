from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_BOOTSTRAP_ROOT = _PROJECT_ROOT / "server" / "eidolon_admin_server" / "bootstrap"


def test_bootstrap_never_imports_full_stack_or_operator_app() -> None:
    forbidden = (
        "eidolon_admin_server.app",
        "eidolon_data",
        "eidolon_memory",
        "nats",
        "supervisor",
        "torch",
        "uvicorn",
    )
    violations: list[str] = []

    for path in sorted(_BOOTSTRAP_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                if any(
                    name == item or name.startswith(f"{item}.") for item in forbidden
                ):
                    violations.append(
                        f"{path.relative_to(_PROJECT_ROOT)} imports {name}"
                    )

    assert violations == []


def test_application_service_depends_on_ports_not_concrete_adapters() -> None:
    service_path = _BOOTSTRAP_ROOT / "service.py"
    service = service_path.read_text()
    service_tree = ast.parse(service, filename=str(service_path))
    state_port = (_BOOTSTRAP_ROOT / "ports" / "state_store.py").read_text()

    port_imports = {
        alias.name
        for node in ast.walk(service_tree)
        if isinstance(node, ast.ImportFrom)
        and node.level == 1
        and node.module == "ports"
        for alias in node.names
    }
    adapter_imports = {
        node.module
        for node in ast.walk(service_tree)
        if isinstance(node, ast.ImportFrom)
        and node.module is not None
        and "adapters" in node.module.split(".")
    }

    assert {"BootstrapStateStore", "NetworkProvisioning"} <= port_imports
    assert adapter_imports == set()
    assert "sqlite" not in state_port.lower()


def test_bootstrap_systemd_unit_is_always_on_and_pre_network_stack() -> None:
    unit_path = _PROJECT_ROOT / "deploy" / "systemd" / "eidolon-bootstrapd.service"
    unit = unit_path.read_text()

    assert "Restart=always" in unit
    assert "StartLimitIntervalSec=0" in unit
    assert "Type=notify" in unit
    assert "WatchdogSec=30s" in unit
    assert "Before=eidolon-stack.service" in unit
    assert "network-online.target" not in unit
    assert "User=eidolon-bootstrap" in unit
    assert "StateDirectoryMode=0710" in unit
    assert "CapabilityBoundingSet=\n" in unit
    assert unit.index("EnvironmentFile=-/etc/eidolon/bootstrap.env") < unit.index(
        "Environment=EIDOLON_BOOTSTRAP_MODE=production"
    )
    daemon = (_BOOTSTRAP_ROOT / "daemon.py").read_text()
    assert "settings.state_dir.chmod(0o710)" in daemon


def test_local_api_and_admin_have_distinct_entrypoints() -> None:
    pyproject = (_PROJECT_ROOT / "pyproject.toml").read_text()

    assert (
        'eidolon-bootstrapd = "eidolon_admin_server.bootstrap.daemon:main"' in pyproject
    )
    assert (
        'eidolon-bootstrap-preflight = "eidolon_admin_server.bootstrap.pi_preflight:main"'
        in pyproject
    )
    assert 'eidolon-local-api = "eidolon_admin_server.local_api.cli:main"' in pyproject
    assert 'eidolon-admin = "eidolon_admin_server.app.cli:main"' in pyproject
    assert (
        'eidolon-lifecycle-workflow = '
        '"eidolon_admin_server.lifecycle_workflow.daemon:main"' in pyproject
    )


def test_admin_systemd_unit_is_loopback_only_and_unprivileged() -> None:
    unit = (_PROJECT_ROOT / "deploy" / "systemd" / "eidolon-admin.service").read_text()

    assert "User=eidolon\n" in unit
    assert "Type=notify\n" in unit
    assert "NotifyAccess=main\n" in unit
    assert "TimeoutStartSec=20s\n" in unit
    assert "Group=eidolon-lifecycle-client\n" in unit
    assert "SupplementaryGroups=eidolon\n" in unit
    assert "EIDOLON_ADMIN_API_HOST=127.0.0.1\n" in unit
    assert "EIDOLON_ADMIN_STATE_DIR=/var/lib/eidolon/admin\n" in unit
    assert "EIDOLON_ADMIN_SYSTEM_DIRECTORY_UDS=/run/eidolon/system.sock\n" in unit
    assert "StateDirectory=eidolon/admin\n" in unit
    assert "StateDirectoryMode=0700\n" in unit
    assert "RuntimeDirectory=eidolon-removal-capability\n" in unit
    assert "RuntimeDirectoryMode=0750\n" in unit
    assert "ExecStartPre=" not in unit
    assert "SupplementaryGroups=eidolon-bootstrap" not in unit
    assert "CapabilityBoundingSet=\n" in unit
    assert "RestrictSUIDSGID=yes\n" in unit
    assert (
        "ExecStart=/opt/eidolon/current/eidolon_admin/.venv/bin/eidolon-admin\n" in unit
    )
    assert "EnvironmentFile=/etc/eidolon/host.env\n" in unit


def test_local_api_socket_access_is_scoped_to_its_systemd_process() -> None:
    unit = (
        _PROJECT_ROOT / "deploy" / "systemd" / "eidolon-local-api.service"
    ).read_text()
    deployment_notes = (_PROJECT_ROOT / "deploy" / "systemd" / "README.md").read_text()

    assert "User=eidolon-local-api\n" in unit
    assert "Group=eidolon-local-api\n" in unit
    assert "SupplementaryGroups=eidolon-bootstrap eidolon-lifecycle-client\n" in unit
    assert "EIDOLON_LOCAL_API_LIFECYCLE_WORKFLOW_SOCKET=" in unit
    assert "Environment=EIDOLON_LOCAL_API_HOST=0.0.0.0\n" in unit
    assert "ReadOnlyPaths=/var/lib/eidolon-bootstrap/commissioning_tls.pem\n" in unit
    assert "no persistent membership" in deployment_notes
    assert "cannot list" in deployment_notes


def test_lifecycle_workflow_is_a_distinct_hardened_principal() -> None:
    unit = (
        _PROJECT_ROOT
        / "deploy"
        / "systemd"
        / "eidolon-lifecycle-workflow.service"
    ).read_text()

    assert "Type=notify\n" in unit
    assert "Requires=eidolon-admin.service\n" in unit
    assert "After=local-fs.target eidolond.service eidolon-admin.service\n" in unit
    assert "User=eidolon-lifecycle\n" in unit
    assert "Group=eidolon-lifecycle-client\n" in unit
    assert "SupplementaryGroups=eidolon-lifecycle eidolon\n" in unit
    assert "RuntimeDirectory=eidolon-lifecycle\n" in unit
    assert "RuntimeDirectoryMode=0750\n" in unit
    assert "ExecStartPre=" not in unit
    assert "StateDirectory=eidolon-lifecycle\n" in unit
    assert "StateDirectoryMode=0700\n" in unit
    assert "UMask=0077\n" in unit
    assert "RestrictSUIDSGID=yes\n" in unit
    assert "EIDOLON_LIFECYCLE_ALLOWED_LOCAL_API_USER=eidolon-local-api" in unit
    assert "EIDOLON_ADMIN_LOCAL_API_SERVICE_TOKEN" not in unit


def test_local_api_is_discoverable_only_as_a_pinned_https_candidate() -> None:
    service = (
        _PROJECT_ROOT / "deploy" / "avahi" / "eidolon-local-api.service"
    ).read_text()

    assert "<type>_eidolon-local-api._tcp</type>" in service
    assert "<port>9002</port>" in service
    assert '<service protocol="ipv4">' in service
    assert "<txt-record>scheme=https</txt-record>" in service


def test_bootstrap_help_does_not_initialize_full_stack() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "eidolon_admin_server.bootstrap.daemon",
            "--help",
        ],
        cwd=_PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "host bootstrap control plane" in result.stdout
