from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from eidolon_admin_server.app.tools.mobile.schemas import MobileJobRequest
from eidolon_admin_server.app.tools.mobile.service import MobileToolService


def _service(tmp_path: Path) -> MobileToolService:
    client_root = tmp_path / "eidolon_client_mobile"
    script = client_root / "scripts/android-mobile.sh"
    adb = tmp_path / "sdk/platform-tools/adb"
    flutter = tmp_path / "flutter/bin/flutter"
    script.parent.mkdir(parents=True)
    adb.parent.mkdir(parents=True)
    flutter.parent.mkdir(parents=True)
    script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    adb.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    flutter.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)
    adb.chmod(0o755)
    flutter.chmod(0o755)
    return MobileToolService(
        client_root=client_root,
        jobs_root=tmp_path / "jobs",
        job_logs_root=tmp_path / "logs",
        flutter_path=flutter,
        android_sdk_root=tmp_path / "sdk",
        adb_path=adb,
    )


def test_devices_parse_adb_metadata_and_stable_eidolon_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    def capture(args: list[str], *, timeout: int, check: bool = True) -> str:
        joined = " ".join(args)
        if "devices -l" in joined:
            return (
                "List of devices attached\n"
                "df331f93 device usb:0-2 product:muyu model:24091RPADC "
                "device:muyu transport_id:1\n"
            )
        if "settings get secure android_id" in joined:
            return "aabbccddeeff0011\n"
        if "pidof" in joined:
            return "20980\n"
        return "device\n"

    monkeypatch.setattr(service, "_capture", capture)
    devices = service.devices()

    assert len(devices) == 1
    assert devices[0].serial == "df331f93"
    assert devices[0].model == "24091RPADC"
    assert devices[0].app_running is True
    assert devices[0].app_pid == 20980
    assert (
        devices[0].eidolon_device_id
        == "mobile-android-48c115de19928d8dceb165c1da240869"
    )


def test_action_commands_are_fixed_and_do_not_use_a_shell(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    monkeypatch.setattr(service, "_require_online_device", lambda serial: None)

    install = service._command(MobileJobRequest(action="install", serial="df331f93"))
    reinstall = service._command(
        MobileJobRequest(action="reinstall", serial="df331f93", mode="release")
    )

    assert install[-4:] == ["--mode", "debug", "--serial", "df331f93"]
    assert install[1] == "install"
    assert reinstall[1] == "reinstall"
    assert "release" in reinstall


@pytest.mark.asyncio
async def test_diagnose_job_runs_and_persists_log(tmp_path: Path) -> None:
    service = _service(tmp_path)
    job = await service.create_job(MobileJobRequest(action="diagnose"))
    # Process creation can be slower under the full integration suite; keep a
    # bounded five-second deadline instead of assuming one-second scheduling.
    for _ in range(250):
        current = service.get_job(job.id)
        if current.status in {"succeeded", "failed", "cancelled"}:
            break
        await asyncio.sleep(0.02)

    current = service.get_job(job.id)
    assert current.status == "succeeded"
    assert Path(current.log_path).exists()
    assert "job finished: succeeded" in Path(current.log_path).read_text(
        encoding="utf-8"
    )
