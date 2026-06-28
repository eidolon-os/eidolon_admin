from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator

from . import catalog
from .schemas import (
    Esp32Action,
    Esp32Artifact,
    Esp32Backup,
    Esp32BoardInfo,
    Esp32BoardProfile,
    Esp32EnvironmentStatus,
    Esp32Job,
    Esp32JobRequest,
    Esp32JobStatus,
    Esp32Partition,
    Esp32Port,
    Esp32ProbeResult,
)


SERIAL_PATTERNS = (
    "/dev/cu.usbmodem*",
    "/dev/cu.wchusbserial*",
    "/dev/cu.SLAB_USBtoUART*",
    "/dev/cu.usbserial*",
    "/dev/ttyUSB*",
    "/dev/ttyACM*",
)
SERIAL_TAKEOVER_TIMEOUT_SECONDS = 3
STALE_SERIAL_RESERVATION_SECONDS = 15


class Esp32ToolError(Exception):
    pass


class Esp32JobConflict(Esp32ToolError):
    pass


class Esp32NotFound(Esp32ToolError):
    pass


@dataclass
class CommandStep:
    args: list[str]
    cwd: Path
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class JobRecord:
    model: Esp32Job
    steps: list[CommandStep]
    port: str | None
    subscribers: list[asyncio.Queue[str | None]] = field(default_factory=list)
    process: asyncio.subprocess.Process | None = None
    cancel_requested: bool = False


@dataclass
class PortUse:
    kind: str
    board_id: str
    owner_id: str
    started_at: str
    can_takeover: bool = False
    close_requested: asyncio.Event = field(default_factory=asyncio.Event)
    close_reason: str | None = None
    serial_port: object | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Esp32ToolService:
    def __init__(self, *, jobs_root: Path | None = None, catalog_file: Path | None = None) -> None:
        self.catalog_file = catalog_file
        self.client_root = catalog.catalog_client_root(catalog_file=catalog_file)
        self.jobs_root = jobs_root or catalog.ADMIN_ROOT / "var/esp32-tools/jobs"
        self.jobs_root.mkdir(parents=True, exist_ok=True)
        self.backups_root = self.jobs_root / "backups"
        self.backups_root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.jobs_root / "index.jsonl"
        self._jobs: dict[str, JobRecord] = {}
        self._order: list[str] = []
        self._serial_sessions: dict[str, PortUse] = {}
        self._lock = asyncio.Lock()
        self._load_history()

    def boards(self) -> list[Esp32BoardProfile]:
        return catalog.board_profiles(self.catalog_file)

    def board(self, board_id: str) -> Esp32BoardProfile:
        board = catalog.find_board(board_id, self.catalog_file)
        if board is None:
            raise Esp32NotFound(f"ESP32 board not found: {board_id}")
        return board

    def ports(self) -> list[Esp32Port]:
        self._drop_stale_serial_reservations()
        busy = self._busy_ports()
        detected = self._pyserial_ports()
        if detected:
            return [self._mark_port_busy(port, index == 0, busy) for index, port in enumerate(detected)]

        seen: set[str] = set()
        paths: list[str] = []
        import glob

        for pattern in SERIAL_PATTERNS:
            for candidate in sorted(glob.glob(pattern)):
                if candidate in seen:
                    continue
                if candidate.startswith("/dev/tty."):
                    cu = "/dev/cu." + candidate.removeprefix("/dev/tty.")
                    if Path(cu).exists():
                        continue
                seen.add(candidate)
                paths.append(candidate)
        return [self._mark_port_busy(Esp32Port(path=path), index == 0, busy) for index, path in enumerate(paths)]

    def environment(self) -> Esp32EnvironmentStatus:
        esptool = catalog.configured_esptool(self.catalog_file)
        idf_py = catalog.configured_idf_py(self.catalog_file)
        idf_export = catalog.configured_idf_export(self.catalog_file)
        idf_path = catalog.configured_idf_path(self.catalog_file) or os.environ.get("IDF_PATH")
        warnings: list[str] = []
        if not self.client_root.exists():
            warnings.append("eidolon-client-esp32 directory not found")
        if not idf_py and not idf_path:
            warnings.append("ESP-IDF not detected; build scripts may still auto-load scripts/eidolon/idf.path")
        if not esptool:
            warnings.append("esptool not found in PATH; erase/chip diagnostics may fail")
        return Esp32EnvironmentStatus(
            client_root=str(self.client_root),
            client_root_exists=self.client_root.exists(),
            idf_available=bool(idf_py or idf_path),
            idf_path=idf_path,
            idf_export_path=idf_export,
            idf_py_path=idf_py,
            esptool_available=bool(esptool),
            esptool_path=esptool,
            boards=[
                {
                    "id": board.id,
                    "label": board.label,
                    "script_exists": Path(board.script_path).exists(),
                    "partition_csv_exists": Path(board.partition_csv).exists(),
                }
                for board in self.boards()
            ],
            warnings=warnings,
        )

    def board_info(self, board_id: str) -> Esp32BoardInfo:
        board = self.board(board_id)
        partitions = catalog.read_partitions(board.partition_csv)
        return Esp32BoardInfo(
            profile=board,
            script_exists=Path(board.script_path).exists(),
            build_dir_exists=Path(board.build_dir).exists(),
            sdkconfig_exists=Path(board.sdkconfig).exists(),
            partition_csv_exists=Path(board.partition_csv).exists(),
            partitions=partitions,
            artifacts=self._artifacts(Path(board.build_dir), board.id),
            backups=self._backups(board.id),
        )

    async def create_job(self, req: Esp32JobRequest) -> Esp32Job:
        board = self.board(req.board_id)
        self._validate_request(board, req)
        steps = self._steps(board, req)
        preview = " && ".join(" ".join(_quote(a) for a in step.args) for step in steps) or req.action
        job_id = uuid.uuid4().hex[:12]
        log_path = self.jobs_root / f"{job_id}.log"
        model = Esp32Job(
            id=job_id,
            board_id=board.id,
            action=req.action,
            status="queued",
            port=req.port,
            command_preview=preview,
            log_path=str(log_path),
            phase="queued",
            progress_total=max(len(steps), 1),
        )
        record = JobRecord(model=model, steps=steps, port=req.port)
        async with self._lock:
            self._assert_no_conflict(record)
            self._jobs[job_id] = record
            self._order.insert(0, job_id)
            self._order = self._order[:100]
            self._persist_job(record)
        asyncio.create_task(self._run_job(record, board, req))
        return record.model

    def list_jobs(self) -> list[Esp32Job]:
        return [self._jobs[job_id].model for job_id in self._order if job_id in self._jobs]

    def get_job(self, job_id: str) -> Esp32Job:
        record = self._jobs.get(job_id)
        if record is None:
            raise Esp32NotFound(f"ESP32 job not found: {job_id}")
        return record.model

    async def cancel_job(self, job_id: str) -> Esp32Job:
        record = self._jobs.get(job_id)
        if record is None:
            raise Esp32NotFound(f"ESP32 job not found: {job_id}")
        record.cancel_requested = True
        if record.process and record.process.returncode is None:
            record.process.terminate()
        self._persist_job(record)
        return record.model

    async def stream_job(self, job_id: str) -> AsyncIterator[str]:
        record = self._jobs.get(job_id)
        if record is None:
            raise Esp32NotFound(f"ESP32 job not found: {job_id}")
        path = Path(record.model.log_path)
        if path.exists():
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                yield line
        if record.model.status not in ("queued", "running"):
            return
        queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=1000)
        record.subscribers.append(queue)
        try:
            while True:
                item = await queue.get()
                if item is None:
                    return
                yield item
        finally:
            if queue in record.subscribers:
                record.subscribers.remove(queue)

    async def serial_stream(self, board_id: str, port: str, baud: int | None, *, takeover: bool = False) -> AsyncIterator[str]:
        board = self.board(board_id)
        serial_baud = baud or board.default_baud
        session = await self._reserve_serial_monitor(board.id, port, takeover=takeover)
        serial_port = None
        try:
            try:
                import serial  # type: ignore[import-not-found]
            except Exception as exc:  # noqa: BLE001
                raise Esp32ToolError("pyserial is not installed; serial monitor is unavailable") from exc

            yield f">> opening serial monitor: {port} @ {serial_baud}"
            try:
                serial_port = await asyncio.to_thread(serial.Serial, port=port, baudrate=serial_baud, timeout=0.25)
                session.serial_port = serial_port
            except Exception as exc:  # noqa: BLE001
                raise Esp32ToolError(f"failed to open serial port {port}: {exc}") from exc
            yield ">> serial monitor started"
            while not session.close_requested.is_set():
                raw = await asyncio.to_thread(serial_port.readline)
                if not raw:
                    continue
                yield raw.decode(errors="replace").rstrip("\r\n")
            yield f">> serial monitor stopped: {session.close_reason or 'closed'}"
        finally:
            async with self._lock:
                if self._serial_sessions.get(port) is session:
                    self._serial_sessions.pop(port, None)
            if serial_port is not None:
                await asyncio.to_thread(serial_port.close)

    async def probe_device(self, board_id: str, port: str, baud: int | None) -> Esp32ProbeResult:
        board = self.board(board_id)
        req = Esp32JobRequest(board_id=board_id, action="chip_id", port=port, baud=baud or board.default_baud)
        session = PortUse(kind="probe", board_id=board.id, owner_id="probe", started_at=_now(), can_takeover=False)
        async with self._lock:
            self._assert_port_available(board.id, port)
            self._serial_sessions[port] = session
        try:
            raw_log: list[str] = []
            chip_id = await self._capture_esptool_value(board, req, "chip_id", raw_log)
            flash_id = await self._capture_esptool_value(board, req, "flash_id", raw_log)
            mac = await self._capture_esptool_value(board, req, "read_mac", raw_log)
            return Esp32ProbeResult(
                board_id=board_id,
                port=port,
                baud=baud or board.default_baud,
                chip_id=_last_hexish(chip_id),
                flash_id=_last_hexish(flash_id),
                mac=_last_mac(mac),
                raw_log=raw_log[-120:],
            )
        finally:
            async with self._lock:
                if self._serial_sessions.get(port) is session:
                    self._serial_sessions.pop(port, None)

    def artifact_path(self, board_id: str, artifact_id: str) -> Path:
        board = self.board(board_id)
        for artifact in self._artifacts(Path(board.build_dir), board.id):
            if artifact.id == artifact_id:
                return Path(artifact.path)
        raise Esp32NotFound(f"ESP32 artifact not found: {artifact_id}")

    def backup_path(self, board_id: str, backup_id: str) -> Path:
        self.board(board_id)
        for backup in self._backups(board_id):
            if backup.id == backup_id:
                return Path(backup.path)
        raise Esp32NotFound(f"ESP32 backup not found: {backup_id}")

    def _validate_request(self, board: Esp32BoardProfile, req: Esp32JobRequest) -> None:
        known = {cap.action: cap for cap in board.capabilities}
        cap = known.get(req.action)
        if cap is None:
            raise Esp32ToolError(f"action {req.action!r} is not supported by {board.label}")
        if req.action == "monitor":
            raise Esp32ToolError("monitor is a live serial stream; use the serial stream endpoint")
        if cap.requires_port and not req.port:
            raise Esp32ToolError(f"action {req.action!r} requires a serial port")
        if cap.dangerous and req.confirm_token != cap.confirm_token:
            raise Esp32ToolError(f"confirmation token required: {cap.confirm_token}")
        no_script_actions = {
            "backup_nvs",
            "backup_config",
            "backup_assets",
            "restore_nvs",
            "erase_flash",
            "erase_nvs",
            "erase_config",
            "erase_assets",
            "chip_id",
            "flash_id",
            "read_mac",
            "image_info",
            "reset_device",
            "diagnose",
        }
        if not Path(board.script_path).exists() and req.action not in no_script_actions:
            raise Esp32ToolError(f"script not found: {board.script_path}")

    def _steps(self, board: Esp32BoardProfile, req: Esp32JobRequest) -> list[CommandStep]:
        script = str(Path(board.script_path))
        base_env = self._tool_env()
        port_env = {**base_env, "EIDOLON_PORT": req.port or ""}
        cwd = self.client_root
        if req.action == "build":
            return [CommandStep([script, "build"], cwd, base_env)]
        if req.action == "build_clean":
            return [CommandStep([script, "clean"], cwd, base_env), CommandStep([script, "build"], cwd, base_env)]
        if req.action == "flash":
            return [CommandStep([script, "flash"], cwd, port_env)]
        if req.action == "flash_app":
            if override := board.action_overrides.get("flash_app"):
                return [CommandStep([script, *override], cwd, port_env)]
            return [self._idf_step(board, req, "app-flash")]
        if req.action == "flash_assets":
            if override := board.action_overrides.get("flash_assets"):
                return [CommandStep([script, *override], cwd, port_env)]
            return [self._idf_step(board, req, "flash", "--only-flash-partition=assets")]
        if req.action == "run":
            return [
                CommandStep([script, "build"], cwd, base_env),
                CommandStep([script, "flash"], cwd, port_env),
            ]
        if req.action == "monitor":
            raise Esp32ToolError("monitor is a live serial stream; use serial_stream()")
        if req.action == "clean":
            return [CommandStep([script, "clean"], cwd, base_env)]
        if req.action == "erase_flash":
            return [self._esptool_step(board, req, "erase_flash")]
        if req.action == "erase_nvs":
            return [self._erase_partition_step(board, req, "nvs")]
        if req.action == "erase_config":
            return [self._erase_partition_step(board, req, "nvs"), self._erase_partition_step(board, req, "otadata")]
        if req.action == "erase_assets":
            return [self._erase_partition_step(board, req, "assets")]
        if req.action == "backup_nvs":
            return [self._backup_partition_step(board, req, "nvs")]
        if req.action == "backup_config":
            return [self._backup_partition_step(board, req, "nvs", label="config-nvs"), self._backup_partition_step(board, req, "otadata", label="config-otadata")]
        if req.action == "backup_assets":
            return [self._backup_partition_step(board, req, "assets")]
        if req.action == "restore_nvs":
            return [self._restore_partition_step(board, req, "nvs")]
        if req.action == "chip_id":
            return [self._esptool_step(board, req, "chip_id")]
        if req.action == "flash_id":
            return [self._esptool_step(board, req, "flash_id")]
        if req.action == "read_mac":
            return [self._esptool_step(board, req, "read_mac")]
        if req.action == "image_info":
            return [self._image_info_step(board)]
        if req.action == "reset_device":
            return [self._esptool_step(board, req, "--after", "hard_reset", "chip_id")]
        if req.action == "diagnose":
            return []
        raise Esp32ToolError(f"unsupported action: {req.action}")

    def _idf_step(self, board: Esp32BoardProfile, req: Esp32JobRequest, *extra: str) -> CommandStep:
        idf_py = catalog.configured_idf_py(self.catalog_file) or "idf.py"
        args = [
            idf_py,
            "-B",
            board.build_dir,
            f"-DIDF_TARGET={board.target}",
            f"-DSDKCONFIG={board.sdkconfig}",
            f"-DBOARD_NAME={Path(board.board_type).name}",
            f"-DBOARD_TYPE={board.board_type}",
        ]
        if req.port:
            args.extend(["-p", req.port])
        args.extend(extra)
        return CommandStep(args, self.client_root, self._tool_env())

    def _esptool_step(self, board: Esp32BoardProfile, req: Esp32JobRequest, *extra: str) -> CommandStep:
        esptool = catalog.configured_esptool(self.catalog_file) or "esptool.py"
        args = [esptool, "--chip", board.target]
        if req.port:
            args.extend(["-p", req.port])
        args.extend(extra)
        return CommandStep(args, self.client_root, self._tool_env())

    def _erase_partition_step(self, board: Esp32BoardProfile, req: Esp32JobRequest, name: str) -> CommandStep:
        partition = self._partition(board, name)
        esptool = catalog.configured_esptool(self.catalog_file) or "esptool.py"
        args = [
            esptool,
            "--chip",
            board.target,
            "-p",
            req.port or "",
            "-b",
            "460800",
            "erase_region",
            partition.offset,
            partition.size,
        ]
        return CommandStep(args, self.client_root, self._tool_env())

    def _backup_partition_step(
        self,
        board: Esp32BoardProfile,
        req: Esp32JobRequest,
        name: str,
        *,
        label: str | None = None,
    ) -> CommandStep:
        partition = self._partition(board, name)
        backup_path = self._new_backup_path(board.id, label or name)
        esptool = catalog.configured_esptool(self.catalog_file) or "esptool.py"
        args = [
            esptool,
            "--chip",
            board.target,
            "-p",
            req.port or "",
            "-b",
            "460800",
            "read_flash",
            partition.offset,
            partition.size,
            str(backup_path),
        ]
        return CommandStep(args, self.client_root, self._tool_env())

    def _restore_partition_step(self, board: Esp32BoardProfile, req: Esp32JobRequest, name: str) -> CommandStep:
        partition = self._partition(board, name)
        backup_id = str(req.options.get("backup_id") or "").strip()
        backup = self.backup_path(board.id, backup_id) if backup_id else self._latest_backup(board.id, name)
        esptool = catalog.configured_esptool(self.catalog_file) or "esptool.py"
        args = [
            esptool,
            "--chip",
            board.target,
            "-p",
            req.port or "",
            "-b",
            "460800",
            "write_flash",
            partition.offset,
            str(backup),
        ]
        return CommandStep(args, self.client_root, self._tool_env())

    def _image_info_step(self, board: Esp32BoardProfile) -> CommandStep:
        artifact = self._latest_firmware_artifact(board)
        esptool = catalog.configured_esptool(self.catalog_file) or "esptool.py"
        return CommandStep([esptool, "image_info", str(artifact)], self.client_root, self._tool_env())

    def _tool_env(self) -> dict[str, str]:
        env: dict[str, str] = {}
        idf_export = catalog.configured_idf_export(self.catalog_file)
        idf_path = catalog.configured_idf_path(self.catalog_file)
        idf_py = catalog.configured_idf_py(self.catalog_file)
        esptool = catalog.configured_esptool(self.catalog_file)
        if idf_export:
            env["EIDOLON_IDF_EXPORT"] = idf_export
        if idf_path:
            env["EIDOLON_IDF_PATH"] = idf_path
            env["IDF_PATH"] = idf_path
        extra_path: list[str] = []
        for path in (idf_py, esptool):
            if path:
                extra_path.append(str(Path(path).parent))
        if extra_path:
            env["PATH"] = os.pathsep.join([*extra_path, os.environ.get("PATH", "")])
        return env

    def _partition(self, board: Esp32BoardProfile, name: str) -> Esp32Partition:
        for partition in catalog.read_partitions(board.partition_csv):
            if partition.name == name:
                if not partition.offset or not partition.size:
                    raise Esp32ToolError(f"partition {name!r} has no offset/size")
                return partition
        raise Esp32ToolError(f"partition {name!r} not found in {board.partition_csv}")

    async def _run_job(self, record: JobRecord, board: Esp32BoardProfile, req: Esp32JobRequest) -> None:
        record.model.status = "running"
        record.model.started_at = _now()
        record.model.phase = "running"
        self._persist_job(record)
        await self._append(record, f">> job {record.model.id} started: {record.model.action}")
        try:
            if req.action == "diagnose":
                record.model.phase = "diagnose"
                record.model.progress_index = 1
                await self._write_diagnose(record, board)
                record.model.exit_code = 0
            else:
                for index, step in enumerate(record.steps, start=1):
                    if record.cancel_requested:
                        raise asyncio.CancelledError()
                    record.model.progress_index = index
                    record.model.phase = self._phase_name(record.model.action, step, index)
                    self._persist_job(record)
                    await self._run_step(record, step)
            if record.cancel_requested:
                record.model.status = "cancelled"
            else:
                record.model.status = "succeeded"
            record.model.phase = record.model.status
            await self._append(record, f">> job {record.model.status}")
        except asyncio.CancelledError:
            record.model.status = "cancelled"
            record.model.error = "cancelled"
            record.model.phase = "cancelled"
            await self._append(record, ">> job cancelled")
        except Exception as exc:  # noqa: BLE001
            record.model.status = "failed"
            record.model.error = str(exc)
            record.model.phase = "failed"
            await self._append(record, f">> job failed: {exc}")
        finally:
            record.model.finished_at = _now()
            self._persist_job(record)
            for queue in list(record.subscribers):
                await queue.put(None)

    async def _run_step(self, record: JobRecord, step: CommandStep) -> None:
        await self._append(record, f">> {' '.join(_quote(a) for a in step.args)}")
        process = await asyncio.create_subprocess_exec(
            *step.args,
            cwd=str(step.cwd),
            env={**os.environ, **step.env},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        record.process = process
        assert process.stdout is not None
        while True:
            raw = await process.stdout.readline()
            if not raw:
                break
            await self._append(record, raw.decode(errors="replace").rstrip("\n"))
        code = await process.wait()
        record.model.exit_code = code
        record.process = None
        if code != 0:
            raise Esp32ToolError(f"command exited with code {code}")
        self._persist_job(record)

    async def _append(self, record: JobRecord, line: str) -> None:
        path = Path(record.model.log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        for queue in list(record.subscribers):
            if queue.full():
                continue
            await queue.put(line)

    async def _write_diagnose(self, record: JobRecord, board: Esp32BoardProfile) -> None:
        env = self.environment()
        info = self.board_info(board.id)
        rows = [
            f"client_root: {env.client_root} exists={env.client_root_exists}",
            f"idf_available: {env.idf_available} path={env.idf_path or '-'}",
            f"idf_export: {env.idf_export_path or '-'}",
            f"idf_py: {env.idf_py_path or '-'}",
            f"esptool_available: {env.esptool_available} path={env.esptool_path or '-'}",
            f"script_exists: {info.script_exists} path={board.script_path}",
            f"partition_csv_exists: {info.partition_csv_exists} path={board.partition_csv}",
            f"build_dir_exists: {info.build_dir_exists} path={board.build_dir}",
            f"sdkconfig_exists: {info.sdkconfig_exists} path={board.sdkconfig}",
        ]
        rows.extend(f"warning: {warning}" for warning in env.warnings)
        for row in rows:
            await self._append(record, row)

    def _assert_no_conflict(self, new_record: JobRecord) -> None:
        for record in self._jobs.values():
            if record.model.status not in ("queued", "running"):
                continue
            if record.model.board_id == new_record.model.board_id:
                raise Esp32JobConflict(f"board {record.model.board_id} is busy")
            if record.port and new_record.port and record.port == new_record.port:
                raise Esp32JobConflict(f"serial port {record.port} is busy")
        if new_record.port and new_record.port in self._serial_sessions:
            use = self._serial_sessions[new_record.port]
            raise Esp32JobConflict(f"serial port {new_record.port} is busy: {use.kind}")

    def _assert_port_available(self, board_id: str, port: str) -> None:
        probe = JobRecord(
            model=Esp32Job(
                id="probe",
                board_id=board_id,
                action="monitor",
                status="queued",
                command_preview="probe",
                log_path="",
            ),
            steps=[],
            port=port,
        )
        self._assert_no_conflict(probe)

    async def _reserve_serial_monitor(self, board_id: str, port: str, *, takeover: bool) -> PortUse:
        while True:
            existing: PortUse | None = None
            async with self._lock:
                self._drop_stale_serial_reservations()
                self._assert_jobs_available(board_id, port)
                existing = self._serial_sessions.get(port)
                if existing is None:
                    session = PortUse(
                        kind="serial_monitor",
                        board_id=board_id,
                        owner_id=uuid.uuid4().hex[:8],
                        started_at=_now(),
                        can_takeover=True,
                    )
                    self._serial_sessions[port] = session
                    return session
                if not takeover or not existing.can_takeover:
                    raise Esp32JobConflict(f"serial port {port} is busy: {existing.kind}")
                existing.close_reason = "takeover requested"
                existing.close_requested.set()
                if existing.serial_port is None and self._serial_sessions.get(port) is existing:
                    self._serial_sessions.pop(port, None)
                    continue
            try:
                await asyncio.wait_for(self._wait_for_port_release(port, existing), timeout=SERIAL_TAKEOVER_TIMEOUT_SECONDS)
            except asyncio.TimeoutError as exc:
                raise Esp32JobConflict(f"serial port {port} did not release after takeover request") from exc

    async def _wait_for_port_release(self, port: str, session: PortUse) -> None:
        while self._serial_sessions.get(port) is session:
            await asyncio.sleep(0.05)

    def _assert_jobs_available(self, board_id: str, port: str) -> None:
        for record in self._jobs.values():
            if record.model.status not in ("queued", "running"):
                continue
            if record.model.board_id == board_id:
                raise Esp32JobConflict(f"board {record.model.board_id} is busy")
            if record.port and record.port == port:
                raise Esp32JobConflict(f"serial port {record.port} is busy: {record.model.action}")

    def _busy_ports(self) -> dict[str, dict[str, str | bool | None]]:
        busy: dict[str, dict[str, str | bool | None]] = {}
        for port, use in self._serial_sessions.items():
            busy[port] = {
                "busy": True,
                "busy_reason": use.kind,
                "busy_owner": use.owner_id,
                "busy_since": use.started_at,
                "can_takeover": use.can_takeover and not use.close_requested.is_set(),
            }
        for record in self._jobs.values():
            if record.port and record.model.status in ("queued", "running"):
                busy[record.port] = {
                    "busy": True,
                    "busy_reason": record.model.action,
                    "busy_owner": record.model.id,
                    "busy_since": record.model.started_at,
                    "can_takeover": False,
                }
        return busy

    def _mark_port_busy(
        self,
        port: Esp32Port,
        selected: bool,
        busy: dict[str, dict[str, str | bool | None]],
    ) -> Esp32Port:
        info = busy.get(port.path)
        if not info:
            return port.model_copy(
                update={
                    "selected": selected,
                    "busy": False,
                    "busy_reason": None,
                    "busy_owner": None,
                    "busy_since": None,
                    "can_takeover": False,
                }
            )
        return port.model_copy(update={"selected": selected, **info})

    def _drop_stale_serial_reservations(self) -> None:
        now = datetime.now(timezone.utc)
        for port, use in list(self._serial_sessions.items()):
            if use.serial_port is not None:
                continue
            try:
                started = datetime.fromisoformat(use.started_at)
            except ValueError:
                started = now
            if use.close_requested.is_set() or (now - started).total_seconds() > STALE_SERIAL_RESERVATION_SECONDS:
                self._serial_sessions.pop(port, None)

    def _artifacts(self, build_dir: Path, board_id: str | None = None) -> list[Esp32Artifact]:
        if not build_dir.exists():
            return []
        artifacts: list[Esp32Artifact] = []
        for path in sorted(build_dir.glob("**/*")):
            if path.suffix.lower() not in {".bin", ".elf", ".map", ".csv"}:
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            kind = _artifact_kind(path)
            artifact_id = _file_id(path)
            artifacts.append(
                Esp32Artifact(
                    id=artifact_id,
                    path=str(path),
                    name=path.name,
                    size=stat.st_size,
                    modified_at=stat.st_mtime,
                    is_firmware=path.suffix.lower() in {".bin", ".elf"},
                    kind=kind,
                    download_url=f"/api/tools/esp32/boards/{board_id or '-'}/artifacts/{artifact_id}/download",
                )
            )
        return artifacts[-40:]

    def _backups(self, board_id: str) -> list[Esp32Backup]:
        root = self.backups_root / _safe_name(board_id)
        if not root.exists():
            return []
        backups: list[Esp32Backup] = []
        for path in sorted(root.glob("*.bin")):
            try:
                stat = path.stat()
            except OSError:
                continue
            backup_id = _file_id(path)
            partition = path.name.split("-", 1)[0]
            backups.append(
                Esp32Backup(
                    id=backup_id,
                    partition=partition,
                    path=str(path),
                    name=path.name,
                    size=stat.st_size,
                    created_at=stat.st_mtime,
                    download_url=f"/api/tools/esp32/boards/{board_id}/backups/{backup_id}/download",
                )
            )
        return backups[-80:]

    def _new_backup_path(self, board_id: str, partition: str) -> Path:
        root = self.backups_root / _safe_name(board_id)
        root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        return root / f"{_safe_name(partition)}-{stamp}.bin"

    def _latest_backup(self, board_id: str, partition: str) -> Path:
        matches = [backup for backup in self._backups(board_id) if backup.partition == partition]
        if not matches:
            raise Esp32ToolError(f"no {partition} backup found for {board_id}")
        return Path(max(matches, key=lambda backup: backup.created_at).path)

    def _latest_firmware_artifact(self, board: Esp32BoardProfile) -> Path:
        artifacts = [
            artifact
            for artifact in self._artifacts(Path(board.build_dir), board.id)
            if artifact.name.endswith(".bin") and artifact.kind in {"app", "firmware", "bin"}
        ]
        if not artifacts:
            raise Esp32ToolError(f"no firmware .bin artifact found in {board.build_dir}")
        return Path(max(artifacts, key=lambda artifact: artifact.modified_at).path)

    def _pyserial_ports(self) -> list[Esp32Port]:
        try:
            from serial.tools import list_ports  # type: ignore[import-not-found]
        except Exception:
            return []
        ports: list[Esp32Port] = []
        for item in sorted(list_ports.comports(), key=lambda p: p.device):
            if not _is_usb_serial_candidate(item.device, item.description or "", item.manufacturer or "", item.vid):
                continue
            vid = f"0x{item.vid:04X}" if item.vid is not None else None
            pid = f"0x{item.pid:04X}" if item.pid is not None else None
            ports.append(
                Esp32Port(
                    path=item.device,
                    description=item.description,
                    manufacturer=item.manufacturer,
                    serial_number=item.serial_number,
                    vid=vid,
                    pid=pid,
                    location=item.location,
                    likely_board_id=_guess_board_id(item.description or "", item.manufacturer or "", vid, pid),
                )
            )
        return ports

    async def _capture_esptool_value(
        self,
        board: Esp32BoardProfile,
        req: Esp32JobRequest,
        action: str,
        raw_log: list[str],
    ) -> str:
        step = self._esptool_step(board, req, action)
        raw_log.append(f">> {' '.join(_quote(a) for a in step.args)}")
        process = await asyncio.create_subprocess_exec(
            *step.args,
            cwd=str(step.cwd),
            env={**os.environ, **step.env},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            out, _ = await asyncio.wait_for(process.communicate(), timeout=20)
        except asyncio.TimeoutError as exc:
            process.kill()
            raise Esp32ToolError(f"{action} timed out") from exc
        text = out.decode(errors="replace")
        raw_log.extend(line.rstrip("\n") for line in text.splitlines())
        if process.returncode:
            raise Esp32ToolError(f"{action} failed with code {process.returncode}")
        return text

    def _phase_name(self, action: Esp32Action, step: CommandStep, index: int) -> str:
        args = " ".join(step.args)
        if "read_flash" in args:
            return "backup"
        if "write_flash" in args:
            return "restore"
        if "erase" in args:
            return "erase"
        if "monitor" in args:
            return "monitor"
        if "flash" in args:
            return "flash"
        if "build" in args:
            return "build"
        return f"{action}:{index}"

    def _load_history(self) -> None:
        if not self.index_path.exists():
            return
        seen: dict[str, Esp32Job] = {}
        for line in self.index_path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                job = Esp32Job.model_validate(json.loads(line))
            except Exception:
                continue
            if job.status in ("queued", "running"):
                job = job.model_copy(update={"status": "cancelled", "error": "admin restarted", "phase": "cancelled"})
            seen[job.id] = job
        for job in sorted(seen.values(), key=lambda item: item.started_at or item.finished_at or "", reverse=True)[:100]:
            self._jobs[job.id] = JobRecord(model=job, steps=[], port=None)
            self._order.append(job.id)

    def _persist_job(self, record: JobRecord) -> None:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        with self.index_path.open("a", encoding="utf-8") as fh:
            fh.write(record.model.model_dump_json() + "\n")


def _quote(value: str) -> str:
    if not value:
        return "''"
    if all(ch.isalnum() or ch in "/._:-=+" for ch in value):
        return value
    return repr(value)


def _file_id(path: Path) -> str:
    return hashlib.sha1(str(path.resolve()).encode("utf-8")).hexdigest()[:16]


def _safe_name(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip())
    return clean.strip("-") or "item"


def _artifact_kind(path: Path) -> str:
    name = path.name.lower()
    if name.endswith(".elf"):
        return "elf"
    if name.endswith(".map"):
        return "map"
    if "partition" in name and name.endswith(".bin"):
        return "partition-table"
    if "bootloader" in name and name.endswith(".bin"):
        return "bootloader"
    if name.endswith(".bin"):
        return "app" if "app" in name or name not in {"bootloader.bin", "partition-table.bin"} else "bin"
    if name.endswith(".csv"):
        return "csv"
    return path.suffix.lower().lstrip(".") or "file"


def _guess_board_id(description: str, manufacturer: str, vid: str | None, pid: str | None) -> str | None:
    text = f"{description} {manufacturer} {vid or ''} {pid or ''}".lower()
    if "esp-box" in text or "box" in text:
        return "esp-box-3"
    if "alientek" in text or "atk" in text or "dnesp32" in text:
        return "atk-dnesp32s3"
    if "waveshare" in text or "amoled" in text:
        return "waveshare-esp32-s3-touch-amoled-206"
    if vid in {"0x303A", "0x10C4", "0x1A86"}:
        return None
    return None


def _is_usb_serial_candidate(device: str, description: str, manufacturer: str, vid: int | None) -> bool:
    if vid is not None:
        return True
    if any(fnmatch.fnmatch(device, pattern) for pattern in SERIAL_PATTERNS):
        return True
    text = f"{device} {description} {manufacturer}".lower()
    allow = ("usb", "uart", "serial", "cp210", "ch340", "wch", "silicon labs", "espressif", "esp32")
    deny = ("bluetooth", "debug-console", "wlan-debug")
    return any(token in text for token in allow) and not any(token in text for token in deny)


def _last_hexish(text: str) -> str | None:
    matches = re.findall(r"0x[0-9A-Fa-f]+", text)
    return matches[-1] if matches else None


def _last_mac(text: str) -> str | None:
    matches = re.findall(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", text)
    return matches[-1].lower() if matches else None
