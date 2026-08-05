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
                if any(name == item or name.startswith(f"{item}.") for item in forbidden):
                    violations.append(f"{path.relative_to(_PROJECT_ROOT)} imports {name}")

    assert violations == []


def test_application_service_depends_on_ports_not_concrete_adapters() -> None:
    service = (_BOOTSTRAP_ROOT / "service.py").read_text()
    state_port = (_BOOTSTRAP_ROOT / "ports" / "state_store.py").read_text()

    assert "from .ports import BootstrapStateStore" in service
    assert "adapters.persistence" not in service
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
    assert "CapabilityBoundingSet=\n" in unit
    assert unit.index("EnvironmentFile=-/etc/eidolon/bootstrap.env") < unit.index(
        "Environment=EIDOLON_BOOTSTRAP_MODE=production"
    )


def test_local_api_and_admin_have_distinct_entrypoints() -> None:
    pyproject = (_PROJECT_ROOT / "pyproject.toml").read_text()

    assert 'eidolon-bootstrapd = "eidolon_admin_server.bootstrap.daemon:main"' in pyproject
    assert (
        'eidolon-bootstrap-preflight = "eidolon_admin_server.bootstrap.pi_preflight:main"'
        in pyproject
    )
    assert 'eidolon-local-api = "eidolon_admin_server.local_api.cli:main"' in pyproject
    assert 'eidolon-admin = "eidolon_admin_server.app.cli:main"' in pyproject


def test_local_api_socket_access_is_scoped_to_its_systemd_process() -> None:
    unit = (
        _PROJECT_ROOT / "deploy" / "systemd" / "eidolon-local-api.service"
    ).read_text()
    deployment_notes = (
        _PROJECT_ROOT / "deploy" / "systemd" / "README.md"
    ).read_text()

    assert "User=eidolon\n" in unit
    assert "SupplementaryGroups=eidolon-bootstrap\n" in unit
    assert "no persistent membership" in deployment_notes


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
