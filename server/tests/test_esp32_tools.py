from __future__ import annotations

import asyncio
import glob
from pathlib import Path

import httpx
import pytest

from eidolon_admin_server.app.main import create_app
from eidolon_admin_server.app.settings import AdminBindConfig, GatewayConfig
from eidolon_admin_server.app.tools.esp32.schemas import Esp32Job, Esp32JobRequest
from eidolon_admin_server.app.tools.esp32.service import (
    Esp32JobConflict,
    Esp32ToolService,
    JobRecord,
)


def _service(tmp_path: Path) -> Esp32ToolService:
    return Esp32ToolService(jobs_root=tmp_path / "jobs")


@pytest.mark.asyncio
async def test_http_lists_three_board_profiles(tmp_path: Path) -> None:
    app = create_app(GatewayConfig(admin=AdminBindConfig(cors_origins=[]), services=[]))
    app.state.esp32_tools = _service(tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://gw",
    ) as ac:
        resp = await ac.get("/api/tools/esp32/boards")
    assert resp.status_code == 200
    boards = resp.json()["boards"]
    assert [board["id"] for board in boards] == [
        "waveshare-esp32-s3-touch-amoled-206",
        "atk-dnesp32s3",
        "esp-box-3",
    ]
    assert all("erase_nvs" in {cap["action"] for cap in board["capabilities"]} for board in boards)


def test_board_catalog_can_be_loaded_from_yaml(tmp_path: Path) -> None:
    client_root = tmp_path / "esp32-client"
    script = client_root / "scripts/eidolon/custom-board.sh"
    partition = client_root / "partitions/custom.csv"
    idf_root = tmp_path / "idf"
    idf_export = idf_root / "export.sh"
    idf_py = idf_root / "tools/idf.py"
    esptool = tmp_path / "idf-python/bin/esptool.py"
    script.parent.mkdir(parents=True)
    partition.parent.mkdir(parents=True)
    idf_py.parent.mkdir(parents=True)
    esptool.parent.mkdir(parents=True)
    script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    idf_export.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    idf_py.write_text("#!/usr/bin/env python\n", encoding="utf-8")
    esptool.write_text("#!/usr/bin/env python\n", encoding="utf-8")
    partition.write_text(
        "# Name, Type, SubType, Offset, Size, Flags\n"
        "nvs,data,nvs,0x9000,0x4000,\n",
        encoding="utf-8",
    )
    catalog_file = tmp_path / "esp32_tools.yaml"
    catalog_file.write_text(
        f"""\
version: 1
client_root: {client_root}
toolchain:
  idf_export: {idf_export}
  idf_path: {idf_root}
  idf_py: {idf_py}
  esptool: {esptool}
boards:
  - id: custom-devkit
    label: Custom DevKit
    vendor: Local
    target: esp32s3
    board_type: custom/devkit
    script_path: $CLIENT_ROOT/scripts/eidolon/custom-board.sh
    build_dir: $CLIENT_ROOT/build/custom
    sdkconfig: $CLIENT_ROOT/build/custom/sdkconfig
    partition_csv: $CLIENT_ROOT/partitions/custom.csv
    default_baud: 921600
""",
        encoding="utf-8",
    )

    svc = Esp32ToolService(jobs_root=tmp_path / "jobs", catalog_file=catalog_file)
    boards = svc.boards()
    assert [board.id for board in boards] == ["custom-devkit"]
    assert boards[0].script_path == str(script.resolve())
    assert boards[0].default_baud == 921600
    environment = svc.environment()
    assert environment.client_root == str(client_root.resolve())
    assert environment.idf_export_path == str(idf_export.resolve())
    assert environment.idf_path == str(idf_root.resolve())
    assert environment.idf_py_path == str(idf_py.resolve())
    assert environment.esptool_path == str(esptool.resolve())


def test_toolchain_config_is_injected_into_commands(tmp_path: Path) -> None:
    client_root = tmp_path / "esp32-client"
    script = client_root / "scripts/eidolon/custom-board.sh"
    partition = client_root / "partitions/custom.csv"
    idf_root = tmp_path / "idf"
    idf_export = idf_root / "export.sh"
    idf_py = idf_root / "tools/idf.py"
    esptool = tmp_path / "idf-python/bin/esptool.py"
    script.parent.mkdir(parents=True)
    partition.parent.mkdir(parents=True)
    idf_py.parent.mkdir(parents=True)
    esptool.parent.mkdir(parents=True)
    script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    idf_export.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    idf_py.write_text("#!/usr/bin/env python\n", encoding="utf-8")
    esptool.write_text("#!/usr/bin/env python\n", encoding="utf-8")
    partition.write_text(
        "# Name, Type, SubType, Offset, Size, Flags\n"
        "nvs,data,nvs,0x9000,0x4000,\n",
        encoding="utf-8",
    )
    catalog_file = tmp_path / "esp32_tools.yaml"
    catalog_file.write_text(
        f"""\
version: 1
client_root: {client_root}
toolchain:
  idf_export: {idf_export}
  idf_path: {idf_root}
  idf_py: {idf_py}
  esptool: {esptool}
boards:
  - id: custom-devkit
    label: Custom DevKit
    vendor: Local
    target: esp32s3
    board_type: custom/devkit
    script_path: $CLIENT_ROOT/scripts/eidolon/custom-board.sh
    build_dir: $CLIENT_ROOT/build/custom
    sdkconfig: $CLIENT_ROOT/build/custom/sdkconfig
    partition_csv: $CLIENT_ROOT/partitions/custom.csv
    default_baud: 921600
""",
        encoding="utf-8",
    )
    svc = Esp32ToolService(jobs_root=tmp_path / "jobs", catalog_file=catalog_file)
    board = svc.board("custom-devkit")

    flash_app = svc._steps(
        board,
        Esp32JobRequest(board_id=board.id, action="flash_app", port="/dev/cu.usbmodem1101"),
    )[0]
    assert flash_app.args[0] == str(idf_py.resolve())
    assert flash_app.env["EIDOLON_IDF_EXPORT"] == str(idf_export.resolve())
    assert flash_app.env["EIDOLON_IDF_PATH"] == str(idf_root.resolve())
    assert flash_app.env["IDF_PATH"] == str(idf_root.resolve())
    assert str(esptool.parent.resolve()) in flash_app.env["PATH"]

    chip_id = svc._steps(
        board,
        Esp32JobRequest(board_id=board.id, action="chip_id", port="/dev/cu.usbmodem1101"),
    )[0]
    assert chip_id.args[0] == str(esptool.resolve())


def test_local_catalog_override_and_action_overrides(tmp_path: Path) -> None:
    client_root = tmp_path / "esp32-client"
    script = client_root / "scripts/custom.sh"
    partition = client_root / "partitions/custom.csv"
    esptool = tmp_path / "tools/esptool.py"
    script.parent.mkdir(parents=True)
    partition.parent.mkdir(parents=True)
    esptool.parent.mkdir(parents=True)
    script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    esptool.write_text("#!/usr/bin/env python\n", encoding="utf-8")
    partition.write_text(
        "# Name, Type, SubType, Offset, Size, Flags\n"
        "nvs,data,nvs,0x9000,0x4000,\n",
        encoding="utf-8",
    )
    catalog_file = tmp_path / "esp32_tools.yaml"
    catalog_file.write_text(
        f"""\
version: 1
client_root: {client_root}
toolchain:
  esptool:
actions:
  flash_app:
    label: App Only
    requires_port: true
boards:
  - id: custom-devkit
    label: Custom DevKit
    vendor: Local
    target: esp32s3
    board_type: custom/devkit
    script_path: $CLIENT_ROOT/scripts/custom.sh
    build_dir: $CLIENT_ROOT/build
    sdkconfig: $CLIENT_ROOT/sdkconfig
    partition_csv: $CLIENT_ROOT/partitions/custom.csv
    capabilities: [flash_app]
    action_overrides:
      flash_app: [flash, --app-only]
""",
        encoding="utf-8",
    )
    (tmp_path / "esp32_tools.local.yaml").write_text(
        f"""\
toolchain:
  esptool: {esptool}
""",
        encoding="utf-8",
    )

    svc = Esp32ToolService(jobs_root=tmp_path / "jobs", catalog_file=catalog_file)
    board = svc.board("custom-devkit")
    assert [cap.action for cap in board.capabilities] == ["flash_app"]
    assert svc.environment().esptool_path == str(esptool.resolve())
    step = svc._steps(
        board,
        Esp32JobRequest(board_id=board.id, action="flash_app", port="/dev/cu.usbmodem1101"),
    )[0]
    assert step.args == [str(script.resolve()), "flash", "--app-only"]


@pytest.mark.asyncio
async def test_diagnose_job_streams_log_lines(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    job = await svc.create_job(
        Esp32JobRequest(
            board_id="esp-box-3",
            action="diagnose",
        )
    )
    for _ in range(30):
        current = svc.get_job(job.id)
        if current.status in {"succeeded", "failed", "cancelled"}:
            break
        await asyncio.sleep(0.05)
    lines = [line async for line in svc.stream_job(job.id)]
    assert any("client_root:" in line for line in lines)
    assert any("script_exists:" in line for line in lines)


@pytest.mark.asyncio
async def test_job_history_survives_service_restart(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    job = await svc.create_job(Esp32JobRequest(board_id="esp-box-3", action="diagnose"))
    for _ in range(30):
        current = svc.get_job(job.id)
        if current.status in {"succeeded", "failed", "cancelled"}:
            break
        await asyncio.sleep(0.05)

    restarted = _service(tmp_path)
    jobs = restarted.list_jobs()
    assert jobs[0].id == job.id
    assert jobs[0].status == "succeeded"


def test_port_scan_deduplicates_and_marks_first(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fake_glob(pattern: str) -> list[str]:
        return {
            "/dev/cu.usbmodem*": ["/dev/cu.usbmodem1101"],
            "/dev/cu.wchusbserial*": ["/dev/cu.wchusbserial2101"],
            "/dev/cu.SLAB_USBtoUART*": [],
            "/dev/cu.usbserial*": ["/dev/cu.usbmodem1101"],
            "/dev/ttyUSB*": ["/dev/ttyUSB0"],
            "/dev/ttyACM*": [],
        }.get(pattern, [])

    monkeypatch.setattr(glob, "glob", fake_glob)
    svc = _service(tmp_path)
    monkeypatch.setattr(svc, "_pyserial_ports", lambda: [])
    ports = svc.ports()
    assert [port.path for port in ports] == [
        "/dev/cu.usbmodem1101",
        "/dev/cu.wchusbserial2101",
        "/dev/ttyUSB0",
    ]
    assert ports[0].selected is True
    assert ports[1].selected is False


def test_erase_nvs_maps_only_to_nvs_partition(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    board = svc.board("waveshare-esp32-s3-touch-amoled-206")
    req = Esp32JobRequest(
        board_id=board.id,
        action="erase_nvs",
        port="/dev/cu.usbmodem1101",
        confirm_token="ERASE NVS",
    )
    steps = svc._steps(board, req)
    assert len(steps) == 1
    args = steps[0].args
    assert "erase_region" in args
    assert "0x9000" in args
    assert "0x4000" in args
    assert "0x8C0000" not in args
    assert "erase_flash" not in args


def test_backup_and_restore_nvs_use_partition_bounds_and_latest_backup(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    board = svc.board("waveshare-esp32-s3-touch-amoled-206")
    req = Esp32JobRequest(
        board_id=board.id,
        action="backup_nvs",
        port="/dev/cu.usbmodem1101",
    )
    backup = svc._steps(board, req)[0]
    assert "read_flash" in backup.args
    assert "0x9000" in backup.args
    assert "0x4000" in backup.args
    assert backup.args[-1].endswith(".bin")

    backup_path = svc.backups_root / board.id / "nvs-20260101-000000.bin"
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path.write_bytes(b"nvs")
    restore = svc._steps(
        board,
        Esp32JobRequest(
            board_id=board.id,
            action="restore_nvs",
            port="/dev/cu.usbmodem1101",
            confirm_token="RESTORE NVS",
        ),
    )[0]
    assert "write_flash" in restore.args
    assert "0x9000" in restore.args
    assert str(backup_path) in restore.args


def test_action_mapping_ignores_user_supplied_shell_options(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    board = svc.board("esp-box-3")
    req = Esp32JobRequest(
        board_id=board.id,
        action="flash",
        port="/dev/cu.usbmodem1101",
        options={"extra": "; rm -rf /"},
    )
    steps = svc._steps(board, req)
    flat = " ".join(arg for step in steps for arg in step.args)
    assert "rm -rf" not in flat
    assert steps[0].args[-1] == "flash"


def test_same_port_running_job_conflicts(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    existing = JobRecord(
        model=Esp32Job(
            id="one",
            board_id="esp-box-3",
            action="monitor",
            status="running",
            command_preview="monitor",
            log_path=str(tmp_path / "one.log"),
        ),
        steps=[],
        port="/dev/cu.usbmodem1101",
    )
    incoming = JobRecord(
        model=Esp32Job(
            id="two",
            board_id="atk-dnesp32s3",
            action="flash",
            status="queued",
            command_preview="flash",
            log_path=str(tmp_path / "two.log"),
        ),
        steps=[],
        port="/dev/cu.usbmodem1101",
    )
    svc._jobs[existing.model.id] = existing
    with pytest.raises(Esp32JobConflict):
        svc._assert_no_conflict(incoming)


@pytest.mark.asyncio
async def test_serial_session_blocks_jobs_on_same_port(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    svc._serial_sessions["/dev/cu.usbmodem1101"] = None
    with pytest.raises(Esp32JobConflict):
        await svc.create_job(
            Esp32JobRequest(
                board_id="esp-box-3",
                action="flash",
                port="/dev/cu.usbmodem1101",
            )
        )
