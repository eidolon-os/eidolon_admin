from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator

from . import catalog
from .schemas import (
    Esp32Action,
    Esp32BoardInfo,
    Esp32BoardProfile,
    Esp32EnvironmentStatus,
    Esp32Job,
    Esp32JobRequest,
    Esp32JobStatus,
    Esp32Partition,
    Esp32Port,
)


SERIAL_PATTERNS = (
    "/dev/cu.usbmodem*",
    "/dev/cu.wchusbserial*",
    "/dev/cu.SLAB_USBtoUART*",
    "/dev/cu.usbserial*",
    "/dev/ttyUSB*",
    "/dev/ttyACM*",
)


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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Esp32ToolService:
    def __init__(self, *, jobs_root: Path | None = None, catalog_file: Path | None = None) -> None:
        self.catalog_file = catalog_file
        self.client_root = catalog.catalog_client_root(catalog_file=catalog_file)
        self.jobs_root = jobs_root or catalog.ADMIN_ROOT / "var/esp32-tools/jobs"
        self.jobs_root.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, JobRecord] = {}
        self._order: list[str] = []
        self._lock = asyncio.Lock()

    def boards(self) -> list[Esp32BoardProfile]:
        return catalog.board_profiles(self.catalog_file)

    def board(self, board_id: str) -> Esp32BoardProfile:
        board = catalog.find_board(board_id, self.catalog_file)
        if board is None:
            raise Esp32NotFound(f"ESP32 board not found: {board_id}")
        return board

    def ports(self) -> list[Esp32Port]:
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
        return [
            Esp32Port(path=path, selected=index == 0)
            for index, path in enumerate(paths)
        ]

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
            artifacts=self._artifacts(Path(board.build_dir)),
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
            command_preview=preview,
            log_path=str(log_path),
        )
        record = JobRecord(model=model, steps=steps, port=req.port)
        async with self._lock:
            self._assert_no_conflict(record)
            self._jobs[job_id] = record
            self._order.insert(0, job_id)
            self._order = self._order[:100]
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

    async def serial_stream(self, board_id: str, port: str, baud: int | None) -> AsyncIterator[str]:
        board = self.board(board_id)
        req = Esp32JobRequest(
            board_id=board_id,
            action="monitor",
            port=port,
            baud=baud or board.default_baud,
        )
        steps = self._steps(board, req)
        if len(steps) != 1:
            raise Esp32ToolError("monitor action resolved to multiple commands")
        step = steps[0]
        yield f">> {' '.join(_quote(a) for a in step.args)}"
        process = await asyncio.create_subprocess_exec(
            *step.args,
            cwd=str(step.cwd),
            env={**os.environ, **step.env},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        assert process.stdout is not None
        try:
            while True:
                raw = await process.stdout.readline()
                if not raw:
                    break
                yield raw.decode(errors="replace").rstrip("\n")
        finally:
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=3)
                except asyncio.TimeoutError:
                    process.kill()

    def _validate_request(self, board: Esp32BoardProfile, req: Esp32JobRequest) -> None:
        known = {cap.action: cap for cap in board.capabilities}
        cap = known.get(req.action)
        if cap is None:
            raise Esp32ToolError(f"action {req.action!r} is not supported by {board.label}")
        if cap.requires_port and not req.port:
            raise Esp32ToolError(f"action {req.action!r} requires a serial port")
        if cap.dangerous and req.confirm_token != cap.confirm_token:
            raise Esp32ToolError(f"confirmation token required: {cap.confirm_token}")
        if not Path(board.script_path).exists() and req.action not in {"chip_id", "flash_id", "diagnose"}:
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
            if board.id == "waveshare-esp32-s3-touch-amoled-206":
                return [CommandStep([script, "flash", "--app-only"], cwd, port_env)]
            return [self._idf_step(board, req, "app-flash")]
        if req.action == "flash_assets":
            if board.id == "waveshare-esp32-s3-touch-amoled-206":
                return [CommandStep([script, "flash", "-p", "assets"], cwd, port_env)]
            return [self._idf_step(board, req, "flash", "--only-flash-partition=assets")]
        if req.action == "run":
            return [
                CommandStep([script, "build"], cwd, base_env),
                CommandStep([script, "flash"], cwd, port_env),
                CommandStep([script, "monitor"], cwd, port_env),
            ]
        if req.action == "monitor":
            return [CommandStep([script, "monitor"], cwd, port_env)]
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
        if req.action == "chip_id":
            return [self._esptool_step(board, req, "chip_id")]
        if req.action == "flash_id":
            return [self._esptool_step(board, req, "flash_id")]
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
        await self._append(record, f">> job {record.model.id} started: {record.model.action}")
        try:
            if req.action == "diagnose":
                await self._write_diagnose(record, board)
                record.model.exit_code = 0
            else:
                for step in record.steps:
                    if record.cancel_requested:
                        raise asyncio.CancelledError()
                    await self._run_step(record, step)
            if record.cancel_requested:
                record.model.status = "cancelled"
            else:
                record.model.status = "succeeded"
            await self._append(record, f">> job {record.model.status}")
        except asyncio.CancelledError:
            record.model.status = "cancelled"
            record.model.error = "cancelled"
            await self._append(record, ">> job cancelled")
        except Exception as exc:  # noqa: BLE001
            record.model.status = "failed"
            record.model.error = str(exc)
            await self._append(record, f">> job failed: {exc}")
        finally:
            record.model.finished_at = _now()
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

    def _artifacts(self, build_dir: Path) -> list[dict[str, str | int | float | bool]]:
        if not build_dir.exists():
            return []
        artifacts: list[dict[str, str | int | float | bool]] = []
        for path in sorted(build_dir.glob("**/*")):
            if path.suffix.lower() not in {".bin", ".elf", ".map", ".csv"}:
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            artifacts.append(
                {
                    "path": str(path),
                    "name": path.name,
                    "size": stat.st_size,
                    "modified_at": stat.st_mtime,
                    "is_firmware": path.suffix.lower() in {".bin", ".elf"},
                }
            )
        return artifacts[-40:]


def _quote(value: str) -> str:
    if not value:
        return "''"
    if all(ch.isalnum() or ch in "/._:-=+" for ch in value):
        return value
    return repr(value)
