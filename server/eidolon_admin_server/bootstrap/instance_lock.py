"""Single-authority process lock for bootstrapd."""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
from typing import IO


class BootstrapAlreadyRunning(RuntimeError):
    """Another process owns the Host bootstrap authority lock."""


class BootstrapInstanceLock:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._file: IO[str] | None = None

    def acquire(self) -> None:
        self._path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
        lock_file = self._path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            lock_file.close()
            raise BootstrapAlreadyRunning(
                "another eidolon-bootstrapd process already owns the Host authority"
            ) from exc
        lock_file.seek(0)
        lock_file.truncate()
        lock_file.write(f"{os.getpid()}\n")
        lock_file.flush()
        os.fsync(lock_file.fileno())
        os.chmod(self._path, 0o640)
        self._file = lock_file

    def release(self) -> None:
        if self._file is None:
            return
        fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        self._file.close()
        self._file = None

    def __enter__(self) -> BootstrapInstanceLock:
        self.acquire()
        return self

    def __exit__(self, *_: object) -> None:
        self.release()
