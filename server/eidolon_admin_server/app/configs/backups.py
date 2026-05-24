"""Backup rotation for config edits.

Before every PUT we snapshot the current file to `<path>.bak.<unix-ts>`.
The most recent 10 backups are kept; older ones are deleted.

Restoration is just an atomic-rename from a chosen backup to the live path
(re-creating a fresh backup of whatever was there first).
"""
from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

_MAX_BACKUPS = 10


@dataclass
class Backup:
    path: Path        # full path of the .bak file
    timestamp: int    # unix seconds (matches filename suffix)
    size: int


def _backup_glob(target: Path) -> str:
    return f"{target.name}.bak.*"


def list_backups(target: Path) -> list[Backup]:
    if not target.parent.exists():
        return []
    out: list[Backup] = []
    for p in target.parent.glob(_backup_glob(target)):
        try:
            ts = int(p.suffix.lstrip("."))
        except ValueError:
            continue
        try:
            size = p.stat().st_size
        except OSError:
            continue
        out.append(Backup(path=p, timestamp=ts, size=size))
    out.sort(key=lambda b: b.timestamp, reverse=True)
    return out


def snapshot(target: Path) -> Backup | None:
    """Copy target → target.bak.<ts>. Returns the new backup or None if the
    file doesn't exist yet (first write — nothing to back up)."""
    if not target.exists():
        return None
    ts = int(time.time())
    bak = target.parent / f"{target.name}.bak.{ts}"
    shutil.copy2(target, bak)
    _rotate(target)
    return Backup(path=bak, timestamp=ts, size=bak.stat().st_size)


def _rotate(target: Path) -> None:
    backups = list_backups(target)
    for old in backups[_MAX_BACKUPS:]:
        try:
            old.path.unlink()
        except OSError:
            pass


def restore(target: Path, timestamp: int) -> Backup:
    """Restore target from `<target>.bak.<timestamp>`. Snapshots the current
    file first so the restore itself is reversible."""
    src = target.parent / f"{target.name}.bak.{timestamp}"
    if not src.exists():
        raise FileNotFoundError(f"no such backup: {src.name}")
    snapshot(target)  # save current before overwriting
    # Atomic replace.
    tmp = target.parent / f".restore.{int(time.time())}.tmp"
    shutil.copy2(src, tmp)
    os.replace(tmp, target)
    return Backup(path=src, timestamp=timestamp, size=src.stat().st_size)
