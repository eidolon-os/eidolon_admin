"""Read-only Raspberry Pi evidence collector for Bootstrap Phase 1."""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol, Sequence


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str = ""


class CommandRunner(Protocol):
    def __call__(self, command: Sequence[str]) -> CommandResult: ...


@dataclass(frozen=True, slots=True)
class PreflightCheck:
    name: str
    ok: bool
    blocking: bool
    detail: str


def _run(command: Sequence[str]) -> CommandResult:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return CommandResult(returncode=127, stdout="", stderr=str(exc))
    return CommandResult(
        returncode=result.returncode,
        stdout=result.stdout.strip(),
        stderr=result.stderr.strip(),
    )


def _service_check(runner: CommandRunner, service: str) -> PreflightCheck:
    result = runner(("systemctl", "is-active", service))
    active = result.returncode == 0 and result.stdout == "active"
    return PreflightCheck(
        name=f"service:{service}",
        ok=active,
        blocking=True,
        detail=result.stdout or result.stderr or f"exit={result.returncode}",
    )


def _parse_os_release(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        values[key] = value.strip().strip('"')
    return values


def collect_preflight(
    *,
    runner: CommandRunner = _run,
    machine: str | None = None,
    os_release_path: Path = Path("/etc/os-release"),
) -> dict:
    checks: list[PreflightCheck] = []
    architecture = machine or platform.machine()
    checks.append(
        PreflightCheck(
            name="architecture",
            ok=architecture == "aarch64",
            blocking=True,
            detail=architecture,
        )
    )

    os_release = _parse_os_release(os_release_path)
    checks.append(
        PreflightCheck(
            name="os-release",
            ok=bool(os_release),
            blocking=True,
            detail=" ".join(
                value
                for value in (
                    os_release.get("PRETTY_NAME", ""),
                    os_release.get("VERSION_CODENAME", ""),
                )
                if value
            )
            or "missing /etc/os-release",
        )
    )

    checks.extend(
        (
            _service_check(runner, "NetworkManager.service"),
            _service_check(runner, "bluetooth.service"),
        )
    )

    nmcli_version = runner(("nmcli", "--version"))
    checks.append(
        PreflightCheck(
            name="networkmanager-cli",
            ok=nmcli_version.returncode == 0,
            blocking=True,
            detail=nmcli_version.stdout or nmcli_version.stderr,
        )
    )
    nm_devices = runner(("nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device"))
    wifi_rows = [
        row for row in nm_devices.stdout.splitlines() if ":wifi:" in row
    ]
    checks.append(
        PreflightCheck(
            name="networkmanager-wifi-device",
            ok=nm_devices.returncode == 0 and bool(wifi_rows),
            blocking=True,
            detail="; ".join(wifi_rows)
            or nm_devices.stderr
            or "no NetworkManager Wi-Fi device",
        )
    )

    bluetooth_version = runner(("bluetoothctl", "--version"))
    checks.append(
        PreflightCheck(
            name="bluez-cli",
            ok=bluetooth_version.returncode == 0,
            blocking=True,
            detail=bluetooth_version.stdout or bluetooth_version.stderr,
        )
    )
    bluetooth_controllers = runner(("bluetoothctl", "list"))
    controller_rows = [
        row
        for row in bluetooth_controllers.stdout.splitlines()
        if row.startswith("Controller ")
    ]
    checks.append(
        PreflightCheck(
            name="bluez-controller",
            ok=bluetooth_controllers.returncode == 0 and bool(controller_rows),
            blocking=True,
            detail="; ".join(controller_rows)
            or bluetooth_controllers.stderr
            or "no BlueZ controller",
        )
    )

    for service in ("hostapd.service", "dnsmasq.service"):
        result = runner(("systemctl", "is-active", service))
        active = result.returncode == 0 and result.stdout == "active"
        checks.append(
            PreflightCheck(
                name=f"legacy-network-service:{service}",
                ok=not active,
                blocking=False,
                detail=(
                    "active; record configuration before choosing the product baseline"
                    if active
                    else result.stdout or result.stderr or "inactive"
                ),
            )
        )

    return {
        "ok": all(check.ok for check in checks if check.blocking),
        "checks": [asdict(check) for check in checks],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="eidolon-bootstrap-preflight",
        description="Read-only Raspberry Pi Bootstrap Phase 1 evidence collector",
    )
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    report = collect_preflight()
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2 if args.pretty else None,
            sort_keys=True,
        )
    )
    raise SystemExit(0 if report["ok"] else 1)


if __name__ == "__main__":
    main()
