from __future__ import annotations

import asyncio
import hashlib
import os
import shlex
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator

from .schemas import (
    MobileCapability,
    MobileDevice,
    MobileEnvironmentStatus,
    MobileJob,
    MobileJobRequest,
)


ADMIN_ROOT = Path(__file__).resolve().parents[5]
MONOREPO_ROOT = ADMIN_ROOT.parent
DEFAULT_CLIENT_ROOT = MONOREPO_ROOT / "eidolon_client_mobile"
DEFAULT_SCRIPT = DEFAULT_CLIENT_ROOT / "scripts" / "android-mobile.sh"
PACKAGE_NAME = "live.eidolon.eidolon_client_mobile"
DEVICE_ID_NAMESPACE = "eidolon-mobile-android-v1"

CAPABILITIES = [
    MobileCapability(action="build", label="编译 APK", description="编译 Android APK"),
    MobileCapability(
        action="install",
        label="安装",
        requires_device=True,
        description="覆盖安装 APK 并保留应用数据",
    ),
    MobileCapability(
        action="reinstall",
        label="重新安装",
        requires_device=True,
        dangerous=True,
        description="卸载应用后进行干净安装；设备 ID 保持不变",
    ),
    MobileCapability(
        action="restart",
        label="重启客户端",
        requires_device=True,
        description="强制停止并重新启动客户端",
    ),
    MobileCapability(
        action="run",
        label="编译 + 安装 + 启动",
        requires_device=True,
        description="执行完整开发部署流程",
    ),
    MobileCapability(
        action="clear_logs",
        label="清空 ADB 日志",
        requires_device=True,
        description="清空设备 logcat 缓冲区",
    ),
    MobileCapability(
        action="diagnose", label="环境诊断", description="检查移动端开发环境"
    ),
]


class MobileToolError(Exception):
    pass


class MobileJobConflict(MobileToolError):
    pass


class MobileNotFound(MobileToolError):
    pass


@dataclass
class JobRecord:
    model: MobileJob
    args: list[str]
    subscribers: list[asyncio.Queue[str | None]] = field(default_factory=list)
    process: asyncio.subprocess.Process | None = None
    cancel_requested: bool = False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MobileToolService:
    def __init__(
        self,
        *,
        client_root: Path | None = None,
        script_path: Path | None = None,
        jobs_root: Path | None = None,
        job_logs_root: Path | None = None,
        flutter_path: Path | None = None,
        android_sdk_root: Path | None = None,
        java_home: Path | None = None,
        adb_path: Path | None = None,
    ) -> None:
        self.client_root = (client_root or DEFAULT_CLIENT_ROOT).expanduser().resolve()
        self.script_path = (
            (script_path or self.client_root / "scripts/android-mobile.sh")
            .expanduser()
            .resolve()
        )
        self.flutter_path = (
            (
                flutter_path
                or Path(
                    os.environ.get(
                        "EIDOLON_FLUTTER_BIN", "~/Developer/flutter/bin/flutter"
                    )
                )
            )
            .expanduser()
            .resolve()
        )
        self.android_sdk_root = (
            (
                android_sdk_root
                or Path(os.environ.get("ANDROID_SDK_ROOT", "~/Developer/Android/sdk"))
            )
            .expanduser()
            .resolve()
        )
        self.java_home = (
            (
                java_home
                or Path(
                    os.environ.get(
                        "JAVA_HOME",
                        "/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home",
                    )
                )
            )
            .expanduser()
            .resolve()
        )
        self.adb_path = (
            (
                adb_path
                or Path(
                    os.environ.get(
                        "EIDOLON_ADB_BIN",
                        str(self.android_sdk_root / "platform-tools/adb"),
                    )
                )
            )
            .expanduser()
            .resolve()
        )
        self.jobs_root = jobs_root or ADMIN_ROOT / "var/mobile-tools/jobs"
        self.jobs_root.mkdir(parents=True, exist_ok=True)
        self.job_logs_root = job_logs_root or (
            self.jobs_root
            if jobs_root is not None
            else Path.home() / "eidolon/logs/admin/mobile-tools/jobs"
        )
        self.job_logs_root.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, JobRecord] = {}
        self._order: list[str] = []
        self._lock = asyncio.Lock()
        self._load_history()

    def environment(self, mode: str = "debug") -> MobileEnvironmentStatus:
        apk_path = self._apk_path(mode)
        warnings: list[str] = []
        checks = (
            (self.client_root.exists(), "eidolon_client_mobile directory not found"),
            (self.script_path.exists(), "Android mobile tool script not found"),
            (os.access(self.flutter_path, os.X_OK), "Flutter executable not found"),
            (self.android_sdk_root.exists(), "Android SDK directory not found"),
            (
                os.access(self.java_home / "bin/java", os.X_OK),
                "JDK executable not found",
            ),
            (os.access(self.adb_path, os.X_OK), "ADB executable not found"),
        )
        warnings.extend(message for okay, message in checks if not okay)
        return MobileEnvironmentStatus(
            client_root=str(self.client_root),
            client_root_exists=self.client_root.exists(),
            script_path=str(self.script_path),
            script_exists=self.script_path.exists(),
            flutter_path=str(self.flutter_path),
            flutter_available=os.access(self.flutter_path, os.X_OK),
            android_sdk_root=str(self.android_sdk_root),
            android_sdk_exists=self.android_sdk_root.exists(),
            java_home=str(self.java_home),
            java_available=os.access(self.java_home / "bin/java", os.X_OK),
            adb_path=str(self.adb_path),
            adb_available=os.access(self.adb_path, os.X_OK),
            apk_path=str(apk_path),
            apk_exists=apk_path.exists(),
            package_name=PACKAGE_NAME,
            capabilities=CAPABILITIES,
            warnings=warnings,
        )

    def devices(self) -> list[MobileDevice]:
        if not os.access(self.adb_path, os.X_OK):
            return []
        output = self._capture([str(self.adb_path), "devices", "-l"], timeout=8)
        devices: list[MobileDevice] = []
        for line in output.splitlines()[1:]:
            fields = line.strip().split()
            if len(fields) < 2:
                continue
            serial, state = fields[:2]
            attrs = {
                key: value
                for field in fields[2:]
                if ":" in field
                for key, value in [field.split(":", 1)]
            }
            device = MobileDevice(
                serial=serial,
                state=state,
                selected=not devices and state == "device",
                product=attrs.get("product"),
                model=attrs.get("model"),
                device=attrs.get("device"),
                transport_id=attrs.get("transport_id"),
            )
            if state == "device":
                self._enrich_device(device)
            devices.append(device)
        return devices

    async def create_job(self, req: MobileJobRequest) -> MobileJob:
        self._validate_request(req)
        args = self._command(req)
        job_id = uuid.uuid4().hex[:12]
        log_path = self.job_logs_root / f"{job_id}.log"
        model = MobileJob(
            id=job_id,
            action=req.action,
            status="queued",
            serial=req.serial,
            mode=req.mode,
            command_preview=" ".join(shlex.quote(arg) for arg in args),
            log_path=str(log_path),
        )
        record = JobRecord(model=model, args=args)
        async with self._lock:
            if any(
                item.model.status in ("queued", "running")
                for item in self._jobs.values()
            ):
                raise MobileJobConflict("another Mobile tool job is already running")
            self._jobs[job_id] = record
            self._order.insert(0, job_id)
            self._order = self._order[:100]
            self._persist(record)
        asyncio.create_task(self._run_job(record))
        return model

    def list_jobs(self) -> list[MobileJob]:
        return [
            self._jobs[job_id].model for job_id in self._order if job_id in self._jobs
        ]

    def get_job(self, job_id: str) -> MobileJob:
        record = self._jobs.get(job_id)
        if record is None:
            raise MobileNotFound(f"Mobile job not found: {job_id}")
        return record.model

    async def cancel_job(self, job_id: str) -> MobileJob:
        record = self._jobs.get(job_id)
        if record is None:
            raise MobileNotFound(f"Mobile job not found: {job_id}")
        record.cancel_requested = True
        if record.process and record.process.returncode is None:
            record.process.terminate()
        self._persist(record)
        return record.model

    async def stream_job(self, job_id: str) -> AsyncIterator[str]:
        record = self._jobs.get(job_id)
        if record is None:
            raise MobileNotFound(f"Mobile job not found: {job_id}")
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

    async def log_stream(self, serial: str) -> AsyncIterator[str]:
        self._require_online_device(serial)
        pid = self._capture(
            [str(self.adb_path), "-s", serial, "shell", "pidof", PACKAGE_NAME],
            timeout=5,
            check=False,
        ).strip()
        if not pid:
            raise MobileToolError("mobile client is not running; restart it first")
        args = [
            str(self.adb_path),
            "-s",
            serial,
            "logcat",
            f"--pid={pid}",
            "-v",
            "threadtime",
        ]
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            yield f">> ADB logcat started: serial={serial} pid={pid}"
            assert process.stdout is not None
            while True:
                raw = await process.stdout.readline()
                if not raw:
                    break
                yield raw.decode(errors="replace").rstrip("\r\n")
        finally:
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=2)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()

    def _validate_request(self, req: MobileJobRequest) -> None:
        requires_device = next(
            cap.requires_device for cap in CAPABILITIES if cap.action == req.action
        )
        if requires_device and not req.serial:
            raise MobileToolError(f"action {req.action!r} requires an Android device")
        if req.serial:
            self._require_online_device(req.serial)
        if not self.script_path.exists():
            raise MobileToolError(f"mobile tool script not found: {self.script_path}")

    def _command(self, req: MobileJobRequest) -> list[str]:
        args = [str(self.script_path), req.action.replace("_", "-"), "--mode", req.mode]
        if req.serial:
            args.extend(["--serial", req.serial])
        if req.skip_build and req.action == "run":
            args.append("--skip-build")
        return args

    def _require_online_device(self, serial: str) -> None:
        if not os.access(self.adb_path, os.X_OK):
            raise MobileToolError(f"ADB executable not found: {self.adb_path}")
        state = self._capture(
            [str(self.adb_path), "-s", serial, "get-state"],
            timeout=5,
            check=False,
        ).strip()
        if state != "device":
            raise MobileNotFound(f"Android device is not online: {serial}")

    def _enrich_device(self, device: MobileDevice) -> None:
        serial = device.serial
        android_id = self._capture(
            [
                str(self.adb_path),
                "-s",
                serial,
                "shell",
                "settings",
                "get",
                "secure",
                "android_id",
            ],
            timeout=5,
            check=False,
        ).strip()
        if android_id and android_id != "null":
            digest = hashlib.sha256(
                f"{DEVICE_ID_NAMESPACE}:{android_id}".encode()
            ).hexdigest()
            device.android_id = android_id
            device.eidolon_device_id = f"mobile-android-{digest[:32]}"
        pid = self._capture(
            [str(self.adb_path), "-s", serial, "shell", "pidof", PACKAGE_NAME],
            timeout=5,
            check=False,
        ).strip()
        if pid:
            device.app_running = True
            try:
                device.app_pid = int(pid.split()[0])
            except ValueError:
                device.app_pid = None

    def _apk_path(self, mode: str) -> Path:
        return self.client_root / f"build/app/outputs/flutter-apk/app-{mode}.apk"

    def _capture(
        self,
        args: list[str],
        *,
        timeout: int,
        check: bool = True,
    ) -> str:
        try:
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            if check:
                raise MobileToolError(str(exc)) from exc
            return ""
        if check and result.returncode != 0:
            raise MobileToolError(result.stderr.strip() or result.stdout.strip())
        return result.stdout

    async def _run_job(self, record: JobRecord) -> None:
        model = record.model
        model.status = "running"
        model.started_at = _now()
        self._persist(record)
        await self._append(record, f">> job {model.id} started: {model.action}")
        env = {
            **os.environ,
            "EIDOLON_FLUTTER_BIN": str(self.flutter_path),
            "ANDROID_SDK_ROOT": str(self.android_sdk_root),
            "EIDOLON_ADB_BIN": str(self.adb_path),
            "JAVA_HOME": str(self.java_home),
        }
        try:
            process = await asyncio.create_subprocess_exec(
                *record.args,
                cwd=self.client_root,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            record.process = process
            assert process.stdout is not None
            while True:
                raw = await process.stdout.readline()
                if not raw:
                    break
                await self._append(record, raw.decode(errors="replace").rstrip("\r\n"))
            model.exit_code = await process.wait()
            if record.cancel_requested:
                model.status = "cancelled"
            elif model.exit_code == 0:
                model.status = "succeeded"
            else:
                model.status = "failed"
                model.error = f"command exited with code {model.exit_code}"
        except asyncio.CancelledError:
            model.status = "cancelled"
        except Exception as exc:
            model.status = "failed"
            model.error = str(exc)
            await self._append(record, f"[error] {exc}")
        finally:
            model.finished_at = _now()
            record.process = None
            self._persist(record)
            await self._append(record, f">> job finished: {model.status}")
            for queue in list(record.subscribers):
                await queue.put(None)

    async def _append(self, record: JobRecord, line: str) -> None:
        path = Path(record.model.log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        for queue in list(record.subscribers):
            try:
                queue.put_nowait(line)
            except asyncio.QueueFull:
                pass

    def _persist(self, record: JobRecord) -> None:
        path = self.jobs_root / f"{record.model.id}.json"
        path.write_text(record.model.model_dump_json(indent=2), encoding="utf-8")

    def _load_history(self) -> None:
        paths = sorted(
            self.jobs_root.glob("*.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )[:100]
        for path in paths:
            try:
                model = MobileJob.model_validate_json(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if model.status in ("queued", "running"):
                model.status = "failed"
                model.error = "admin restarted before the job completed"
                model.finished_at = _now()
            self._jobs[model.id] = JobRecord(model=model, args=[])
            self._order.append(model.id)
