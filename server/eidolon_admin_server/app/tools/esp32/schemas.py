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
    "backup_nvs",
    "backup_config",
    "backup_assets",
    "restore_nvs",
    "chip_id",
    "flash_id",
    "read_mac",
    "image_info",
    "reset_device",
    "diagnose",
]

Esp32JobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]


class Esp32Capability(BaseModel):
    action: Esp32Action
    label: str
    requires_port: bool = False
    dangerous: bool = False
    confirm_token: str | None = None
    description: str | None = None


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
    action_overrides: dict[str, list[str]] = Field(default_factory=dict)


class Esp32Port(BaseModel):
    path: str
    selected: bool = False
    source: Literal["detected", "manual"] = "detected"
    description: str | None = None
    manufacturer: str | None = None
    serial_number: str | None = None
    vid: str | None = None
    pid: str | None = None
    location: str | None = None
    likely_board_id: str | None = None
    busy: bool = False


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
    artifacts: list["Esp32Artifact"]
    backups: list["Esp32Backup"]


class Esp32Artifact(BaseModel):
    id: str
    path: str
    name: str
    size: int
    modified_at: float
    is_firmware: bool
    kind: str
    download_url: str


class Esp32Backup(BaseModel):
    id: str
    partition: str
    path: str
    name: str
    size: int
    created_at: float
    download_url: str


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
    port: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    exit_code: int | None = None
    command_preview: str
    log_path: str
    error: str | None = None
    phase: str | None = None
    progress_index: int = 0
    progress_total: int = 0


class Esp32ProbeResult(BaseModel):
    board_id: str
    port: str
    baud: int
    chip_id: str | None = None
    flash_id: str | None = None
    mac: str | None = None
    raw_log: list[str] = Field(default_factory=list)


class Esp32JobsResponse(BaseModel):
    jobs: list[Esp32Job]


class Esp32BoardsResponse(BaseModel):
    boards: list[Esp32BoardProfile]


class Esp32PortsResponse(BaseModel):
    ports: list[Esp32Port]
