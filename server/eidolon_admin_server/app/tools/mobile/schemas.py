from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


MobileAction = Literal[
    "build",
    "install",
    "reinstall",
    "restart",
    "run",
    "clear_logs",
    "diagnose",
]
MobileBuildMode = Literal["debug", "profile", "release"]
MobileJobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]


class MobileCapability(BaseModel):
    action: MobileAction
    label: str
    requires_device: bool = False
    dangerous: bool = False
    description: str = ""


class MobileDevice(BaseModel):
    serial: str
    state: str
    selected: bool = False
    product: str | None = None
    model: str | None = None
    device: str | None = None
    transport_id: str | None = None
    android_id: str | None = None
    eidolon_device_id: str | None = None
    app_running: bool = False
    app_pid: int | None = None


class MobileEnvironmentStatus(BaseModel):
    client_root: str
    client_root_exists: bool
    script_path: str
    script_exists: bool
    flutter_path: str
    flutter_available: bool
    android_sdk_root: str
    android_sdk_exists: bool
    java_home: str
    java_available: bool
    adb_path: str
    adb_available: bool
    apk_path: str
    apk_exists: bool
    package_name: str
    capabilities: list[MobileCapability]
    warnings: list[str] = Field(default_factory=list)


class MobileJobRequest(BaseModel):
    action: MobileAction
    serial: str | None = None
    mode: MobileBuildMode = "debug"
    skip_build: bool = False


class MobileJob(BaseModel):
    id: str
    action: MobileAction
    status: MobileJobStatus
    serial: str | None = None
    mode: MobileBuildMode = "debug"
    started_at: str | None = None
    finished_at: str | None = None
    exit_code: int | None = None
    command_preview: str
    log_path: str
    error: str | None = None


class MobileDevicesResponse(BaseModel):
    devices: list[MobileDevice]


class MobileJobsResponse(BaseModel):
    jobs: list[MobileJob]
