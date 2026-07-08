"""Owner data backup before destructive deletion."""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from eidolon_data import DataStore
from eidolon_data.schema.models import (
    BodyCommandRow,
    CompanionRow,
    ConversationRow,
    DeviceRow,
    EventRow,
    JobRow,
    MemoryRealmRow,
    MessageRow,
    OwnerRow,
    PersonaGenomeRow,
    RuntimeCallerRow,
    RuntimeSessionRow,
    TurnRow,
)
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..memory.runners import memory_palace_path
from .owner_delete_finalizer import _safe_id


async def create_owner_backup(store: DataStore, owner_id: str) -> dict[str, Any]:
    """Create a structured owner backup and memory-palace copy.

    The backup is created before the deletion journal. If this function fails,
    deletion must not continue.
    """

    root = _default_backup_root(store)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup_id = f"{_safe_id(owner_id)}-{timestamp}-{uuid4().hex[:8]}"
    final_dir = root / "owners" / _safe_id(owner_id) / backup_id
    partial_dir = final_dir.with_name(f"{final_dir.name}.partial")
    if partial_dir.exists():
        shutil.rmtree(partial_dir)
    partial_dir.mkdir(parents=True, exist_ok=False)

    try:
        snapshot = await _collect_owner_snapshot(store, owner_id)
        if not snapshot["owners"]:
            raise KeyError(f"owner not found: {owner_id}")

        data_dir = partial_dir / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        data_files: dict[str, str] = {}
        counts: dict[str, int] = {}
        for table, rows in snapshot.items():
            target = data_dir / f"{table}.json"
            _write_json(target, rows)
            data_files[table] = str(target.relative_to(partial_dir))
            counts[table] = len(rows)

        realm_ids = [row["realm_id"] for row in snapshot["memory_realms"]]
        palaces = _copy_memory_palaces(realm_ids, partial_dir / "memory_palaces")
        for palace in palaces:
            if palace.get("target"):
                palace["target"] = _final_path(
                    Path(str(palace["target"])),
                    partial_dir=partial_dir,
                    final_dir=final_dir,
                )
        created_at = datetime.now(timezone.utc).isoformat()
        manifest = {
            "backup_id": backup_id,
            "owner_id": owner_id,
            "created_at": created_at,
            "path": str(final_dir),
            "source": {
                "eidolon_data_sqlite_path": str(
                    Path(store.settings.sqlite_path).expanduser()
                ),
            },
            "counts": counts,
            "data_files": data_files,
            "memory_palaces": palaces,
            "manifest_path": str(final_dir / "manifest.json"),
            "notes": [
                "Created before owner hard-delete.",
                "Contains owner-scoped DB rows and best-effort memory palace copies.",
            ],
        }
        _write_json(partial_dir / "manifest.json", manifest)
        final_dir.parent.mkdir(parents=True, exist_ok=True)
        os.replace(partial_dir, final_dir)
        manifest["path"] = str(final_dir)
        manifest["manifest_path"] = str(final_dir / "manifest.json")
        return manifest
    except Exception:
        shutil.rmtree(partial_dir, ignore_errors=True)
        raise


async def _collect_owner_snapshot(
    store: DataStore,
    owner_id: str,
) -> dict[str, list[dict[str, Any]]]:
    async with store.session_factory() as session:
        owner = await session.get(OwnerRow, owner_id)
        if owner is None:
            return {
                "owners": [],
                "companions": [],
                "persona_genomes": [],
                "memory_realms": [],
                "devices": [],
                "body_commands": [],
                "runtime_callers": [],
                "runtime_sessions": [],
                "conversations": [],
                "turns": [],
                "messages": [],
                "jobs": [],
                "events": [],
            }

        companion_rows = await _rows(
            session,
            select(CompanionRow).where(CompanionRow.owner_id == owner_id),
        )
        companion_ids = [row.companion_id for row in companion_rows]
        device_rows = await _rows(
            session,
            select(DeviceRow).where(DeviceRow.owner_id == owner_id),
        )
        device_ids = [row.device_id for row in device_rows]
        conversation_rows = await _rows(
            session,
            select(ConversationRow).where(ConversationRow.owner_id == owner_id),
        )
        conversation_ids = [row.conversation_id for row in conversation_rows]
        turn_rows = (
            await _rows(
                session,
                select(TurnRow).where(TurnRow.conversation_id.in_(conversation_ids)),
            )
            if conversation_ids
            else []
        )
        turn_ids = [row.turn_id for row in turn_rows]
        body_command_filters = [BodyCommandRow.owner_id == owner_id]
        if companion_ids:
            body_command_filters.append(BodyCommandRow.companion_id.in_(companion_ids))
        if device_ids:
            body_command_filters.extend(
                [
                    BodyCommandRow.device_id.in_(device_ids),
                    BodyCommandRow.source_device_id.in_(device_ids),
                ]
            )

        return {
            "owners": [_row_to_dict(owner)],
            "companions": [_row_to_dict(row) for row in companion_rows],
            "persona_genomes": [
                _row_to_dict(row)
                for row in (
                    await _rows(
                        session,
                        select(PersonaGenomeRow).where(
                            PersonaGenomeRow.companion_id.in_(companion_ids)
                        ),
                    )
                    if companion_ids
                    else []
                )
            ],
            "memory_realms": [
                _row_to_dict(row)
                for row in await _rows(
                    session,
                    select(MemoryRealmRow).where(MemoryRealmRow.owner_id == owner_id),
                )
            ],
            "devices": [_row_to_dict(row) for row in device_rows],
            "body_commands": [
                _row_to_dict(row)
                for row in await _rows(
                    session,
                    select(BodyCommandRow).where(or_(*body_command_filters)),
                )
            ],
            "runtime_callers": [
                _row_to_dict(row)
                for row in await _rows(
                    session,
                    select(RuntimeCallerRow).where(RuntimeCallerRow.owner_id == owner_id),
                )
            ],
            "runtime_sessions": [
                _row_to_dict(row)
                for row in await _rows(
                    session,
                    select(RuntimeSessionRow).where(
                        RuntimeSessionRow.owner_id == owner_id
                    ),
                )
            ],
            "conversations": [_row_to_dict(row) for row in conversation_rows],
            "turns": [_row_to_dict(row) for row in turn_rows],
            "messages": [
                _row_to_dict(row)
                for row in (
                    await _rows(
                        session,
                        select(MessageRow).where(MessageRow.turn_id.in_(turn_ids)),
                    )
                    if turn_ids
                    else []
                )
            ],
            "jobs": [
                _row_to_dict(row)
                for row in await _rows(
                    session,
                    select(JobRow).where(JobRow.owner_id == owner_id),
                )
            ],
            "events": [
                _row_to_dict(row)
                for row in await _rows(
                    session,
                    select(EventRow).where(EventRow.owner_id == owner_id),
                )
            ],
        }


async def _rows(session: AsyncSession, statement: Any) -> list[Any]:
    model = statement.column_descriptions[0].get("entity")
    if model is not None:
        pk_cols = [col for col in model.__mapper__.primary_key]
        if pk_cols:
            statement = statement.order_by(*pk_cols)
    result = await session.scalars(statement)
    return list(result)


def _copy_memory_palaces(realm_ids: list[str], target_root: Path) -> list[dict[str, Any]]:
    palaces: list[dict[str, Any]] = []
    for realm_id in sorted({rid for rid in realm_ids if rid}):
        source = Path(memory_palace_path(realm_id))
        target = target_root / _safe_id(realm_id)
        entry: dict[str, Any] = {
            "realm_id": realm_id,
            "source": str(source),
            "target": str(target),
        }
        if not source.exists():
            entry["palace_missing"] = True
            palaces.append(entry)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target, symlinks=True, ignore_dangling_symlinks=True)
            files = [path for path in target.rglob("*") if path.is_file()]
            entry["files"] = len(files)
            entry["bytes"] = sum(path.stat().st_size for path in files)
        else:
            shutil.copy2(source, target)
            entry["files"] = 1
            entry["bytes"] = target.stat().st_size
        palaces.append(entry)
    return palaces


def _row_to_dict(row: Any) -> dict[str, Any]:
    return {
        column.name: _jsonable(getattr(row, column.name))
        for column in row.__table__.columns
    }


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


def _write_json(path: Path, data: Any) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _final_path(path: Path, *, partial_dir: Path, final_dir: Path) -> str:
    try:
        return str(final_dir / path.relative_to(partial_dir))
    except ValueError:
        return str(path)


def _default_backup_root(store: DataStore) -> Path:
    env = os.environ.get("EIDOLON_OWNER_BACKUP_ROOT", "").strip()
    if env:
        return Path(env).expanduser()
    data_dir = Path(store.settings.sqlite_path).expanduser().resolve().parent
    eidolon_root = data_dir.parent if data_dir.name == "data" else data_dir
    return eidolon_root / "backup"
