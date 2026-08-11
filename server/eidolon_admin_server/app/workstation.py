"""Which workstation tools this Host can actually offer.

Firmware flashing and Android tooling need a developer machine: a serial port,
an ESP-IDF checkout, a Flutter SDK. A product Host has none of that, and the
capability is simply absent there.

Absence must disable the capability, never the control plane. These are
optional developer conveniences; a missing catalog is not a missing authority,
so it may not take the Host's control plane down with it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class WorkstationCapability:
    """One optional tool surface and why it is or is not available."""

    name: str
    available: bool
    detail: str

    def to_wire(self) -> dict[str, object]:
        return {"name": self.name, "available": self.available, "detail": self.detail}


def esp32_capability(catalog_file: Path | None) -> WorkstationCapability:
    """ESP32 firmware and serial tooling needs its catalog and a client tree."""

    if catalog_file is None:
        return WorkstationCapability(
            "esp32-tools", False, "no ESP32 tools catalog is configured"
        )
    if not catalog_file.is_file():
        return WorkstationCapability(
            "esp32-tools",
            False,
            f"ESP32 tools catalog is not present on this Host: {catalog_file}",
        )
    return WorkstationCapability("esp32-tools", True, str(catalog_file))


def mobile_capability(client_root: Path | None) -> WorkstationCapability:
    """Android tooling needs the Mobile client checkout to build from."""

    if client_root is None:
        return WorkstationCapability(
            "mobile-tools", False, "no Mobile client tree is configured"
        )
    if not client_root.is_dir():
        return WorkstationCapability(
            "mobile-tools",
            False,
            f"Mobile client tree is not present on this Host: {client_root}",
        )
    return WorkstationCapability("mobile-tools", True, str(client_root))
