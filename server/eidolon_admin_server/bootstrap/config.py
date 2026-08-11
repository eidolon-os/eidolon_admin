"""Stdlib-only configuration for the always-on bootstrap process."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Mapping

from .domain import SETUP_CODE_DIGITS, is_usable_setup_code


class BootstrapMode(StrEnum):
    DEVELOPMENT = "development"
    PRODUCTION = "production"


class CommissioningAdapter(StrEnum):
    DISABLED = "disabled"
    BLUEZ = "bluez"


class NetworkAdapter(StrEnum):
    MEMORY = "memory"
    NETWORK_MANAGER = "networkmanager"


class BootstrapConfigurationError(ValueError):
    """Raised when bootstrap configuration would weaken a trust boundary."""


_DEFAULT_BLE_SERVICE_UUID = str(
    uuid.uuid5(uuid.NAMESPACE_DNS, "bootstrap.v1.eidolon.local")
)


@dataclass(frozen=True, slots=True)
class BootstrapSettings:
    mode: BootstrapMode
    state_dir: Path
    runtime_dir: Path
    control_socket: Path
    ble_service_uuid: str
    dev_setup_code_ttl_seconds: int = 600
    dev_setup_code: str | None = None
    commissioning_adapter: CommissioningAdapter = CommissioningAdapter.DISABLED
    network_adapter: NetworkAdapter = NetworkAdapter.MEMORY

    @property
    def database_path(self) -> Path:
        return self.state_dir / "bootstrap.sqlite3"

    @property
    def identity_key_path(self) -> Path:
        return self.state_dir / "host_identity.ed25519"

    @property
    def commissioning_tls_pem_path(self) -> Path:
        return self.state_dir / "commissioning_tls.pem"

    @property
    def instance_lock_path(self) -> Path:
        return self.runtime_dir / "bootstrapd.lock"


def load_bootstrap_settings(
    environ: Mapping[str, str] | None = None,
) -> BootstrapSettings:
    env = os.environ if environ is None else environ
    raw_mode = env.get("EIDOLON_BOOTSTRAP_MODE", BootstrapMode.PRODUCTION.value)
    try:
        mode = BootstrapMode(raw_mode.strip().lower())
    except ValueError as exc:
        raise BootstrapConfigurationError(
            "EIDOLON_BOOTSTRAP_MODE must be development or production"
        ) from exc

    default_commissioning = (
        CommissioningAdapter.BLUEZ.value
        if mode is BootstrapMode.PRODUCTION
        else CommissioningAdapter.DISABLED.value
    )
    try:
        commissioning_adapter = CommissioningAdapter(
            env.get("EIDOLON_BOOTSTRAP_COMMISSIONING_ADAPTER", default_commissioning)
            .strip()
            .lower()
        )
    except ValueError as exc:
        raise BootstrapConfigurationError(
            "EIDOLON_BOOTSTRAP_COMMISSIONING_ADAPTER must be disabled or bluez"
        ) from exc
    default_network = (
        NetworkAdapter.NETWORK_MANAGER.value
        if mode is BootstrapMode.PRODUCTION
        else NetworkAdapter.MEMORY.value
    )
    try:
        network_adapter = NetworkAdapter(
            env.get("EIDOLON_BOOTSTRAP_NETWORK_ADAPTER", default_network)
            .strip()
            .lower()
        )
    except ValueError as exc:
        raise BootstrapConfigurationError(
            "EIDOLON_BOOTSTRAP_NETWORK_ADAPTER must be memory or networkmanager"
        ) from exc
    if mode is BootstrapMode.PRODUCTION and (
        commissioning_adapter is not CommissioningAdapter.BLUEZ
        or network_adapter is not NetworkAdapter.NETWORK_MANAGER
    ):
        raise BootstrapConfigurationError(
            "production bootstrap requires bluez and networkmanager adapters"
        )

    if "EIDOLON_BOOTSTRAP_STATE_DIR" in env:
        state_dir = Path(env["EIDOLON_BOOTSTRAP_STATE_DIR"]).expanduser()
    elif mode is BootstrapMode.PRODUCTION:
        state_dir = Path("/var/lib/eidolon-bootstrap")
    else:
        state_dir = Path.home() / "eidolon" / "bootstrap"

    if "EIDOLON_BOOTSTRAP_RUNTIME_DIR" in env:
        runtime_dir = Path(env["EIDOLON_BOOTSTRAP_RUNTIME_DIR"]).expanduser()
    elif mode is BootstrapMode.PRODUCTION:
        runtime_dir = Path("/run/eidolon-bootstrap")
    else:
        runtime_dir = state_dir / "run"

    control_socket = Path(
        env.get("EIDOLON_BOOTSTRAP_CONTROL_SOCKET", str(runtime_dir / "control.sock"))
    ).expanduser()

    raw_ttl = env.get("EIDOLON_BOOTSTRAP_DEV_SETUP_CODE_TTL_SECONDS", "600")
    try:
        ttl = int(raw_ttl)
    except ValueError as exc:
        raise BootstrapConfigurationError(
            "EIDOLON_BOOTSTRAP_DEV_SETUP_CODE_TTL_SECONDS must be an integer"
        ) from exc
    if not 60 <= ttl <= 86400:
        raise BootstrapConfigurationError(
            "development Setup code TTL must be between 60 and 86400 seconds"
        )

    raw_dev_setup_code = env.get("EIDOLON_BOOTSTRAP_DEV_SETUP_CODE")
    dev_setup_code = (
        None if raw_dev_setup_code is None else raw_dev_setup_code.strip()
    )
    if dev_setup_code == "":
        dev_setup_code = None
    if dev_setup_code is not None:
        if mode is not BootstrapMode.DEVELOPMENT:
            raise BootstrapConfigurationError(
                "EIDOLON_BOOTSTRAP_DEV_SETUP_CODE is development-only"
            )
        if not is_usable_setup_code(dev_setup_code):
            raise BootstrapConfigurationError(
                "EIDOLON_BOOTSTRAP_DEV_SETUP_CODE must be a usable "
                f"{SETUP_CODE_DIGITS}-digit Setup code"
            )

    ble_service_uuid = env.get(
        "EIDOLON_BOOTSTRAP_BLE_SERVICE_UUID", _DEFAULT_BLE_SERVICE_UUID
    ).strip()
    try:
        uuid.UUID(ble_service_uuid)
    except ValueError as exc:
        raise BootstrapConfigurationError(
            "EIDOLON_BOOTSTRAP_BLE_SERVICE_UUID must be a UUID"
        ) from exc

    for path in (state_dir, runtime_dir, control_socket):
        if mode is BootstrapMode.PRODUCTION and not path.is_absolute():
            raise BootstrapConfigurationError(
                "production bootstrap paths must be absolute"
            )
    # macOS allows roughly 104 bytes and Linux 108 bytes for AF_UNIX paths.
    # Keep a portable margin and fail before systemd enters a restart loop.
    if len(os.fsencode(control_socket)) > 100:
        raise BootstrapConfigurationError(
            "bootstrap control socket path must be at most 100 encoded bytes"
        )

    return BootstrapSettings(
        mode=mode,
        state_dir=state_dir,
        runtime_dir=runtime_dir,
        control_socket=control_socket,
        ble_service_uuid=ble_service_uuid,
        dev_setup_code_ttl_seconds=ttl,
        dev_setup_code=dev_setup_code,
        commissioning_adapter=commissioning_adapter,
        network_adapter=network_adapter,
    )
