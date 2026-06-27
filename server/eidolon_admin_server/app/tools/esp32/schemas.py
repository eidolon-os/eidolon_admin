from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


Esp32Action = Literal[
    "build",
    "build_clean",
    "flash",
    "flash_app",
    "flash_assets",
    "run",
    "monitor",
    "clean",
    "erase_flash",
    "erase_nvs",
    "erase_config",
    "erase_assets",
    "chip_id",
    "flash_id",
    "diagnose",
]

Esp32JobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]


class Esp32Capability(BaseModel):
    action: Esp32Action
    label: str
    requires_port: bool = False
    dangerous: bool = False
    confirm_token: str | None = None


class Esp32Partition(BaseModel):
    name: str
    offset: str
    size: str
    description: str | None = None


class Esp32BoardProfile(BaseModel):
    id: str
    label: str
    vendor: str
    target: str
    board_type: str
    script_path: str
    build_dir: str
    sdkconfig: str
    partition_csv: str
    default_baud: int = 115200
    capabilities: list[Esp32Capability]


class Esp32Port(BaseModel):
    path: str
    selected: bool = False
    source: Literal["detected", "manual"] = "detected"


class Esp32EnvironmentStatus(BaseModel):
    client_root: str
    client_root_exists: bool
    idf_available: bool
    idf_path: str | None = None
    idf_export_path: str | None = None
    idf_py_path: str | None = None
    esptool_available: bool
    esptool_path: str | None = None
    boards: list[dict[str, str | bool]]
    warnings: list[str] = Field(default_factory=list)


class Esp32BoardInfo(BaseModel):
    profile: Esp32BoardProfile
    script_exists: bool
    build_dir_exists: bool
    sdkconfig_exists: bool
    partition_csv_exists: bool
    partitions: list[Esp32Partition]
    artifacts: list[dict[str, str | int | float | bool]]


class Esp32JobRequest(BaseModel):
    board_id: str
    action: Esp32Action
    port: str | None = None
    baud: int | None = Field(default=None, ge=1, le=2_000_000)
    confirm_token: str | None = None
    options: dict[str, str | int | bool | None] = Field(default_factory=dict)


class Esp32Job(BaseModel):
    id: str
    board_id: str
    action: Esp32Action
    status: Esp32JobStatus
    started_at: str | None = None
    finished_at: str | None = None
    exit_code: int | None = None
    command_preview: str
    log_path: str
    error: str | None = None


class Esp32JobsResponse(BaseModel):
    jobs: list[Esp32Job]


class Esp32BoardsResponse(BaseModel):
    boards: list[Esp32BoardProfile]


class Esp32PortsResponse(BaseModel):
    ports: list[Esp32Port]
