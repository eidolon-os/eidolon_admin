"""Atomic read / write for memory's users.yaml.

Schema (mirrors memory's UsersConfig):

    users:
      - id: alice
        port: 8030
        enabled: true
        palace_path: ''

We never overwrite the file in place — write to ``<path>.tmp``, fsync, then
``os.replace``. This protects memory-supervisor (which may SIGHUP-reread on
ANY change) from observing a half-written file.
"""
from __future__ import annotations

import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

from .runners import UserEntry, users_yaml_path


class UsersYamlError(ValueError):
    """Raised for schema violations (duplicate id, duplicate port, missing fields)."""


@dataclass
class WriteResult:
    path: Path
    users: list[UserEntry]


def _load(path: Path) -> list[UserEntry]:
    if not path.exists():
        return []
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out: list[UserEntry] = []
    for u in raw.get("users", []) or []:
        if not isinstance(u, dict):
            continue
        uid = u.get("id")
        if not uid:
            continue
        out.append(
            UserEntry(
                id=str(uid),
                port=int(u.get("port", 0) or 0),
                enabled=bool(u.get("enabled", True)),
                palace_path=str(u.get("palace_path", "") or ""),
            )
        )
    return out


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


def _serialize(users: list[UserEntry]) -> str:
    data = {
        "users": [
            {
                "id": u.id,
                "port": u.port,
                "enabled": u.enabled,
                "palace_path": u.palace_path,
            }
            for u in users
        ]
    }
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


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


# -- public API ---------------------------------------------------------------


def read_users(path: Path | None = None) -> list[UserEntry]:
    return _load(path or users_yaml_path())


def upsert_user(entry: UserEntry, *, path: Path | None = None) -> WriteResult:
    target = path or users_yaml_path()
    users = _load(target)
    # Replace by id, else append.
    by_id = {u.id: i for i, u in enumerate(users)}
    if entry.id in by_id:
        users[by_id[entry.id]] = entry
    else:
        users.append(entry)
    _validate(users)
    _atomic_write(target, _serialize(users))
    return WriteResult(path=target, users=users)


def set_enabled(user_id: str, enabled: bool, *, path: Path | None = None) -> WriteResult:
    target = path or users_yaml_path()
    users = _load(target)
    for u in users:
        if u.id == user_id:
            u.enabled = enabled
            break
    else:
        raise UsersYamlError(f"unknown user: {user_id!r}")
    _validate(users)
    _atomic_write(target, _serialize(users))
    return WriteResult(path=target, users=users)


def get_user(user_id: str, *, path: Path | None = None) -> UserEntry | None:
    for u in _load(path or users_yaml_path()):
        if u.id == user_id:
            return u
    return None
