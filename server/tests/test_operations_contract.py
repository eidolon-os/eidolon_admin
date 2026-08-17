"""Admin's operations contract against Admin's own configuration.

The claim worth checking hardest here is the separation of accounts: the
bootstrap daemon holds the Host identity and runs as its own user, and the
Owner-facing services do not. That separation is only real if every file the
identity depends on is owned by that account, which is what these check.
"""

from __future__ import annotations

import tomllib
from dataclasses import fields
from pathlib import Path
from urllib.parse import urlparse

import pytest

from eidolon_admin_server.bootstrap.config import BootstrapMode, BootstrapSettings
from eidolon_admin_server.local_api.config import LocalApiSettings

_REPOSITORY = Path(__file__).resolve().parents[2]
_CONTRACT = _REPOSITORY / "ops/component.toml"
_UNITS = _REPOSITORY / "deploy/systemd"
_BOOTSTRAP_STATE = Path("/var/lib/eidolon-bootstrap")


@pytest.fixture(scope="module")
def contract() -> dict:
    return tomllib.loads(_CONTRACT.read_text(encoding="utf-8"))


def _unit(contract: dict, unit_id: str) -> dict:
    return next(unit for unit in contract["units"] if unit["id"] == unit_id)


def _input(contract: dict, name: str) -> dict:
    return next(entry for entry in contract["inputs"] if entry["name"] == name)


def test_the_declared_bootstrap_database_is_the_one_it_opens(contract: dict) -> None:
    settings = BootstrapSettings(
        mode=BootstrapMode.PRODUCTION,
        state_dir=_BOOTSTRAP_STATE,
        runtime_dir=Path("/run/eidolon-bootstrap"),
        control_socket=Path("/run/eidolon-bootstrap/control.sock"),
        ble_service_uuid="179e2e95-b1ee-5aa5-8dcf-7519b6c7ac52",
    )

    declared = {entry["path"] for entry in contract["state"]["authority"]}

    assert str(settings.database_path) in declared


def test_the_declared_ports_are_the_ones_admin_defaults_to(contract: dict) -> None:
    defaults = {field.name: field.default for field in fields(LocalApiSettings)}

    assert contract["ports"]["local_api"]["default"] == defaults["port"]
    # The Local API reaches the control plane by URL rather than by port role,
    # so the two would drift silently. This is where they meet.
    admin_port = urlparse(defaults["admin_base_url"]).port
    assert contract["ports"]["admin"]["default"] == admin_port


def test_the_local_api_is_the_only_thing_declared_reachable_from_the_lan(
    contract: dict,
) -> None:
    reachable = {
        role for role, port in contract["ports"].items() if port.get("bind") == "lan"
    }

    # Admin's control plane and console are loopback. The App reaches the Host
    # through the Local API and nothing else, and a second LAN binding here
    # would be a second front door nobody designed.
    assert reachable == {"local_api"}


def test_the_identity_and_its_daemon_share_one_account(contract: dict) -> None:
    identity = _input(contract, "host_identity")
    daemon = _unit(contract, "eidolon-bootstrapd")

    assert identity["owner"] == identity["group"] == daemon["user"]
    assert daemon["user"] != _unit(contract, "eidolon-local-api")["user"]
    # 0600: readable by that account and by nothing else, including the
    # services that ask it to prove which Host this is.
    assert identity["mode"] == "0600"
    assert Path(identity["install_path"]).is_relative_to(_BOOTSTRAP_STATE)


def test_every_secret_is_readable_only_by_root_until_a_unit_asks(
    contract: dict,
) -> None:
    for entry in contract["inputs"]:
        if entry["kind"] != "secret":
            continue
        assert entry["mode"] == "0600"
        assert entry["owner"] == entry["group"] == "root"


def test_a_factory_reset_takes_the_identity_with_it(contract: dict) -> None:
    removed = [Path(item) for item in contract["reset"]["factory"]]
    identity = Path(_input(contract, "host_identity")["install_path"])

    # This is the line between a reset and a reinstall. A reinstall must leave
    # the identity alone; a reset must not, or the Host that comes back is
    # still the one every previously trusting device knows.
    assert any(identity.is_relative_to(root) for root in removed)


def test_each_declared_unit_matches_the_service_file_shipped_beside_it(
    contract: dict,
) -> None:
    for unit in contract["units"]:
        source = _UNITS / f"{unit['id']}.service"
        assert source.is_file(), f"{source.name} is declared but not shipped"

        body = source.read_text(encoding="utf-8")
        exec_start = next(
            line for line in body.splitlines() if line.startswith("ExecStart=")
        )
        assert f"/eidolon_admin/{unit['exec']} " in f"{exec_start} "
        assert f"User={unit['user']}" in body
