"""Atomic read / write for memory's users.yaml.

Schema mirrors memory's ``UsersConfig`` (including optional ``consolidator``).
Writes go to ``<path>.tmp``, fsync, then ``os.replace`` so memory-supervisor
never reads a half-written file.
"""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .runners import (
    ConsolidatorConfig,
    UserEntry,
    load_users,
    serialize_users,
    users_yaml_path,
)


class UsersYamlError(ValueError):
    """Raised for schema violations (duplicate id, duplicate port, missing fields)."""


@dataclass
class WriteResult:
    path: Path
    users: list[UserEntry]


def _validate_consolidator(cfg: ConsolidatorConfig) -> None:
    if cfg.interval_hours <= 0:
        raise UsersYamlError("consolidator.interval_hours must be > 0")
    if cfg.window_days <= 0:
        raise UsersYamlError("consolidator.window_days must be > 0")
    if cfg.min_drawers < 1:
        raise UsersYamlError("consolidator.min_drawers must be >= 1")
    if not 0.0 <= cfg.min_confidence <= 1.0:
        raise UsersYamlError("consolidator.min_confidence must be in [0, 1]")


def _validate(users: list[UserEntry]) -> None:
    seen_ids: set[str] = set()
    seen_ports: dict[int, str] = {}
    for u in users:
        if u.id in seen_ids:
            raise UsersYamlError(f"duplicate user id: {u.id!r}")
        seen_ids.add(u.id)
        if u.port <= 0:
            raise UsersYamlError(f"user {u.id!r} has invalid port {u.port}")
        if u.port in seen_ports:
            raise UsersYamlError(
                f"port {u.port} reused by users {seen_ports[u.port]!r} and {u.id!r}"
            )
        seen_ports[u.port] = u.id
        if u.consolidator is not None:
            _validate_consolidator(u.consolidator)


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".users-", suffix=".yaml.tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def _merge_upsert(existing: UserEntry, incoming: UserEntry) -> UserEntry:
    """Preserve consolidator when the caller did not specify it."""
    consolidator = incoming.consolidator
    if consolidator is None:
        consolidator = existing.consolidator
    return UserEntry(
        id=incoming.id,
        port=incoming.port,
        enabled=incoming.enabled,
        palace_path=incoming.palace_path,
        consolidator=consolidator,
    )


# -- public API ---------------------------------------------------------------


def read_users(path: Path | None = None) -> list[UserEntry]:
    return load_users(path or users_yaml_path())


def upsert_user(entry: UserEntry, *, path: Path | None = None) -> WriteResult:
    target = path or users_yaml_path()
    users = load_users(target)
    by_id = {u.id: i for i, u in enumerate(users)}
    if entry.id in by_id:
        entry = _merge_upsert(users[by_id[entry.id]], entry)
        users[by_id[entry.id]] = entry
    else:
        users.append(entry)
    _validate(users)
    _atomic_write(target, serialize_users(users))
    return WriteResult(path=target, users=users)


def set_enabled(user_id: str, enabled: bool, *, path: Path | None = None) -> WriteResult:
    target = path or users_yaml_path()
    users = load_users(target)
    for u in users:
        if u.id == user_id:
            u.enabled = enabled
            break
    else:
        raise UsersYamlError(f"unknown user: {user_id!r}")
    _validate(users)
    _atomic_write(target, serialize_users(users))
    return WriteResult(path=target, users=users)


def set_consolidator(
    user_id: str,
    consolidator: ConsolidatorConfig | None,
    *,
    path: Path | None = None,
) -> WriteResult:
    """Set or clear the per-user ``consolidator`` block (``None`` removes it)."""
    target = path or users_yaml_path()
    users = load_users(target)
    for u in users:
        if u.id == user_id:
            if consolidator is not None:
                _validate_consolidator(consolidator)
            u.consolidator = consolidator
            break
    else:
        raise UsersYamlError(f"unknown user: {user_id!r}")
    _validate(users)
    _atomic_write(target, serialize_users(users))
    return WriteResult(path=target, users=users)


def get_user(user_id: str, *, path: Path | None = None) -> UserEntry | None:
    for u in load_users(path or users_yaml_path()):
        if u.id == user_id:
            return u
    return None
