from __future__ import annotations

from pathlib import Path
from typing import Sequence

from eidolon_admin_server.bootstrap.pi_preflight import (
    CommandResult,
    collect_preflight,
)


class FakeRunner:
    def __init__(self, results: dict[tuple[str, ...], CommandResult]) -> None:
        self._results = results

    def __call__(self, command: Sequence[str]) -> CommandResult:
        return self._results.get(
            tuple(command),
            CommandResult(127, "", "command not found"),
        )


def _healthy_results() -> dict[tuple[str, ...], CommandResult]:
    return {
        ("systemctl", "is-active", "NetworkManager.service"): CommandResult(
            0, "active"
        ),
        ("systemctl", "is-active", "bluetooth.service"): CommandResult(0, "active"),
        ("systemctl", "is-active", "hostapd.service"): CommandResult(3, "inactive"),
        ("systemctl", "is-active", "dnsmasq.service"): CommandResult(3, "inactive"),
        ("nmcli", "--version"): CommandResult(0, "nmcli tool, version 1.42.4"),
        ("nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device"): CommandResult(
            0, "wlan0:wifi:disconnected\nlo:loopback:connected"
        ),
        ("bluetoothctl", "--version"): CommandResult(0, "bluetoothctl: 5.66"),
        ("bluetoothctl", "list"): CommandResult(
            0, "Controller AA:BB:CC:DD:EE:FF eidolon-pi [default]"
        ),
    }


def test_pi_preflight_reports_evidence_without_mutations(tmp_path: Path) -> None:
    os_release = tmp_path / "os-release"
    os_release.write_text(
        'PRETTY_NAME="Debian GNU/Linux 12 (bookworm)"\nVERSION_CODENAME=bookworm\n'
    )

    report = collect_preflight(
        runner=FakeRunner(_healthy_results()),
        machine="aarch64",
        os_release_path=os_release,
    )

    assert report["ok"] is True
    assert all(check["ok"] for check in report["checks"])


def test_pi_preflight_blocks_without_wifi_or_bluetooth_controller(
    tmp_path: Path,
) -> None:
    os_release = tmp_path / "os-release"
    os_release.write_text("PRETTY_NAME=RaspberryPiOS\n")
    results = _healthy_results()
    results[("nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device")] = CommandResult(
        0, "eth0:ethernet:connected"
    )
    results[("bluetoothctl", "list")] = CommandResult(0, "")

    report = collect_preflight(
        runner=FakeRunner(results),
        machine="aarch64",
        os_release_path=os_release,
    )

    assert report["ok"] is False
    failed = {check["name"] for check in report["checks"] if not check["ok"]}
    assert failed == {"networkmanager-wifi-device", "bluez-controller"}


def test_pi_preflight_warns_but_does_not_fail_for_existing_raspap_services(
    tmp_path: Path,
) -> None:
    os_release = tmp_path / "os-release"
    os_release.write_text("PRETTY_NAME=RaspberryPiOS\n")
    results = _healthy_results()
    results[("systemctl", "is-active", "hostapd.service")] = CommandResult(0, "active")
    results[("systemctl", "is-active", "dnsmasq.service")] = CommandResult(0, "active")

    report = collect_preflight(
        runner=FakeRunner(results),
        machine="aarch64",
        os_release_path=os_release,
    )

    assert report["ok"] is True
    warnings = [
        check
        for check in report["checks"]
        if check["name"].startswith("legacy-network-service")
    ]
    assert all(check["blocking"] is False and check["ok"] is False for check in warnings)


def test_polkit_rule_grants_only_required_networkmanager_actions() -> None:
    repository = Path(__file__).resolve().parents[2]
    rule = (
        repository / "deploy/polkit/60-eidolon-bootstrap-network.rules"
    ).read_text()

    assert 'subject.user !== "eidolon-bootstrap"' in rule
    assert "org.freedesktop.NetworkManager.checkpoint-rollback" in rule
    assert "org.freedesktop.NetworkManager.network-control" in rule
    assert "org.freedesktop.NetworkManager.settings.modify.system" in rule
    assert "org.freedesktop.NetworkManager.wifi.scan" in rule
    assert "org.freedesktop.NetworkManager.enable-disable-network" not in rule
