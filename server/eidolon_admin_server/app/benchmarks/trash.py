"""Safe file moves for benchmark artifact deletion."""

from __future__ import annotations

import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path


class TrashError(ValueError):
    """Raised when an artifact cannot be safely moved to trash."""


def move_to_trash(
    *,
    source: Path,
    root: Path,
    project: str,
    suite: str,
    run_id: str,
) -> Path:
    source = source.expanduser().resolve()
    root = root.expanduser().resolve()
    _require_inside(source, root)
    if not source.exists():
        raise FileNotFoundError(str(source))
    trash_root = root / ".trash" / _safe_segment(project) / _safe_segment(suite)
    trash_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base_name = f"{_safe_segment(run_id)}.{stamp}"
    target = trash_root / base_name
    if target.exists():
        target = trash_root / f"{base_name}.{uuid.uuid4().hex[:8]}"
    try:
        os.replace(source, target)
    except OSError:
        shutil.move(str(source), str(target))
    return target


def move_file_group_to_trash(
    *,
    sources: list[Path],
    root: Path,
    project: str,
    suite: str,
    run_id: str,
) -> Path:
    root = root.expanduser().resolve()
    resolved = [source.expanduser().resolve() for source in sources]
    if not resolved:
        raise FileNotFoundError(run_id)
    for source in resolved:
        _require_inside(source, root)
    if not resolved[0].exists():
        raise FileNotFoundError(str(resolved[0]))

    trash_root = root / ".trash" / _safe_segment(project) / _safe_segment(suite)
    trash_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base_name = f"{_safe_segment(run_id)}.{stamp}"
    target_dir = trash_root / base_name
    if target_dir.exists():
        target_dir = trash_root / f"{base_name}.{uuid.uuid4().hex[:8]}"
    target_dir.mkdir()
    for source in resolved:
        if source.exists():
            try:
                os.replace(source, target_dir / source.name)
            except OSError:
                shutil.move(str(source), str(target_dir / source.name))
    return target_dir


def _require_inside(path: Path, root: Path) -> None:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise TrashError(f"path is outside benchmark root: {path}") from exc
    if ".trash" in path.parts:
        raise TrashError("refusing to delete an artifact already inside trash")


def _safe_segment(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in value)
    return safe.strip("._") or "run"
