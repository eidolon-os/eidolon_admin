"""Durable owner-delete cleanup journal and finalizer.

Owner rows are deleted from eidolon_data in a single DB transaction, but memory
workers and mempalace directories live outside that transaction. The journal in
this module is written before DB deletion starts so an interrupted delete can be
resumed on the next admin startup or owner-delete request.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from eidolon_data import DataStore

from ..memory.runners import memory_palace_path


def owner_cleanup_counts(result: Any) -> dict[str, int]:
    return {
        "devices": int(getattr(result, "devices", 0) or 0),
        "companions": int(getattr(result, "companions", 0) or 0),
        "persona_genomes": int(getattr(result, "persona_genomes", 0) or 0),
        "memory_realms": int(getattr(result, "memory_realms", 0) or 0),
        "body_commands": int(getattr(result, "body_commands", 0) or 0),
        "runtime_sessions": int(getattr(result, "runtime_sessions", 0) or 0),
        "messages": int(getattr(result, "messages", 0) or 0),
        "turns": int(getattr(result, "turns", 0) or 0),
        "conversations": int(getattr(result, "conversations", 0) or 0),
        "jobs": int(getattr(result, "jobs", 0) or 0),
        "events": int(getattr(result, "events", 0) or 0),
    }


async def purge_memory_realms(
    memory_supervisor_client: Any | None,
    realm_ids: list[str],
) -> dict[str, Any]:
    """Stop orphaned memory workers, then move palaces to the trash directory.

    The function is conservative: if the supervisor says a removed realm is
    still live after reconcile/polling, the palace is left in place and the
    caller can retry later from the journal.
    """

    unique_realm_ids = sorted({rid for rid in realm_ids if rid})
    if not unique_realm_ids:
        return {"purged": True, "reconciled": False, "realms": []}

    reconciled = False
    if memory_supervisor_client is not None:
        try:
            await memory_supervisor_client.reconcile()
            reconciled = True
            pending = set(unique_realm_ids)
            deadline = time.monotonic() + 15.0
            while pending and time.monotonic() < deadline:
                data = await memory_supervisor_client.list_realms()
                live = {
                    (r.get("spec") or {}).get("memory_realm_id")
                    for r in data.get("realms", [])
                }
                pending = {rid for rid in pending if rid in live}
                if pending:
                    await asyncio.sleep(0.5)
            if pending:
                return {
                    "purged": False,
                    "reconciled": reconciled,
                    "pending_live_realms": sorted(pending),
                    "error": "memory realms still live after supervisor reconcile",
                }
        except Exception as exc:  # noqa: BLE001 - journal retry handles this
            return {"purged": False, "reconciled": reconciled, "error": str(exc)}

    trash_root = _default_trash_root()
    realms: list[dict[str, Any]] = []
    for rid in unique_realm_ids:
        entry: dict[str, Any] = {"realm_id": rid}
        try:
            palace = Path(memory_palace_path(rid))
            if palace.exists():
                trash_root.mkdir(parents=True, exist_ok=True)
                target = trash_root / f"{palace.name}_{int(time.time())}"
                counter = 1
                while target.exists():
                    target = trash_root / f"{palace.name}_{int(time.time())}_{counter}"
                    counter += 1
                shutil.move(str(palace), str(target))
                entry["palace_trashed_to"] = str(target)
            else:
                entry["palace_missing"] = True
        except Exception as exc:  # noqa: BLE001 - keep the job pending
            entry["error"] = str(exc)
        realms.append(entry)

    purged = not any("error" in realm for realm in realms)
    return {"purged": purged, "reconciled": reconciled, "realms": realms}


async def finalize_owner_delete_jobs(
    store: DataStore,
    memory_supervisor_client: Any | None,
    agent_runtime_client: Any | None,
    *,
    journal: OwnerDeleteJournal | None = None,
    only_owner_id: str | None = None,
) -> dict[str, Any]:
    """Resume pending owner deletion jobs.

    A job can be pending before DB deletion, after DB deletion, or during memory
    cleanup. Each call makes best effort progress and leaves the journal intact
    if anything still needs attention.
    """

    resolved_journal = journal or OwnerDeleteJournal()
    jobs = [
        job
        for job in resolved_journal.pending()
        if only_owner_id is None or job.get("owner_id") == only_owner_id
    ]
    summaries: list[dict[str, Any]] = []
    finalized = 0
    for job in jobs:
        job["attempts"] = int(job.get("attempts") or 0) + 1
        job["updated_at"] = _now()
        try:
            if not bool(job.get("agent_runtime_deleted")):
                if agent_runtime_client is None:
                    raise RuntimeError(
                        "Agent runtime client unavailable; system deletion is blocked"
                    )
                runtime_result = await agent_runtime_client.delete_owner_runtime(
                    str(job["owner_id"])
                )
                job = resolved_journal.mark_agent_runtime_deleted(
                    job, runtime_result
                )
            if not bool(job.get("db_deleted")):
                result = await store.dev_maintenance.delete_owner_tree(
                    str(job["owner_id"])
                )
                job = resolved_journal.mark_db_deleted(job, result)

            memory = await purge_memory_realms(
                memory_supervisor_client,
                [str(rid) for rid in job.get("realm_ids", [])],
            )
            job["memory"] = memory
            objects = purge_storage_objects(
                store,
                [str(key) for key in job.get("storage_keys", [])],
            )
            job["objects"] = objects
            job["updated_at"] = _now()
            if _memory_cleanup_complete(memory) and bool(objects.get("purged")):
                resolved_journal.complete(job)
                finalized += 1
                summaries.append(
                    {
                        "job_id": job["job_id"],
                        "owner_id": job["owner_id"],
                        "status": "finalized",
                        "memory": memory,
                        "objects": objects,
                    }
                )
            else:
                job["last_error"] = (
                    _object_error_summary(objects)
                    if not bool(objects.get("purged"))
                    else _memory_error_summary(memory)
                )
                resolved_journal.save(job)
                summaries.append(
                    {
                        "job_id": job["job_id"],
                        "owner_id": job["owner_id"],
                        "status": "pending",
                        "error": job["last_error"],
                        "memory": memory,
                        "objects": objects,
                    }
                )
        except Exception as exc:  # noqa: BLE001 - leave journal for retry
            job["last_error"] = str(exc)
            job["updated_at"] = _now()
            resolved_journal.save(job)
            summaries.append(
                {
                    "job_id": job.get("job_id"),
                    "owner_id": job.get("owner_id"),
                    "status": "pending",
                    "error": str(exc),
                }
            )

    remaining_matching = [
        job
        for job in resolved_journal.pending()
        if only_owner_id is None or job.get("owner_id") == only_owner_id
    ]
    return {
        "attempted": len(jobs),
        "finalized": finalized,
        "pending": len(remaining_matching),
        "pending_all": len(resolved_journal.pending()),
        "jobs": summaries,
    }


class OwnerDeleteJournal:
    """File-backed journal for owner delete jobs.

    The pending file is written with ``os.replace`` so a crash either leaves the
    previous valid JSON or the next valid JSON. Completed files are moved aside
    for auditability and ignored by the finalizer.
    """

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root).expanduser() if root is not None else _default_journal_dir()
        self.completed_root = self.root / "completed"

    def create_or_load(
        self,
        *,
        owner_id: str,
        realm_ids: list[str],
        storage_keys: list[str] | None = None,
        backup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        for job in self.pending():
            if job.get("owner_id") == owner_id:
                merged = sorted(
                    {
                        *[str(rid) for rid in job.get("realm_ids", [])],
                        *[str(rid) for rid in realm_ids],
                    }
                )
                job["realm_ids"] = merged
                job["storage_keys"] = sorted(
                    {
                        *[str(key) for key in job.get("storage_keys", [])],
                        *[str(key) for key in storage_keys or []],
                    }
                )
                if backup:
                    job["backup"] = backup
                job["updated_at"] = _now()
                self.save(job)
                return job

        safe_owner = _safe_id(owner_id)
        job = {
            "job_id": f"owner-delete-{safe_owner}-{int(time.time())}-{uuid4().hex[:8]}",
            "kind": "owner_delete",
            "owner_id": owner_id,
            "realm_ids": sorted({str(rid) for rid in realm_ids if rid}),
            "storage_keys": sorted({str(key) for key in storage_keys or [] if key}),
            "db_deleted": False,
            "agent_runtime_deleted": False,
            "memory_purged": False,
            "attempts": 0,
            "created_at": _now(),
            "updated_at": _now(),
            "last_error": "",
        }
        if backup:
            job["backup"] = backup
        self.save(job)
        return job

    def mark_agent_runtime_deleted(
        self,
        job: dict[str, Any],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        job["agent_runtime_deleted"] = True
        job["runtime_counts"] = {
            str(key): int(value or 0)
            for key, value in dict(result.get("counts") or {}).items()
        }
        job["runtime_revocation_keys_written"] = int(
            result.get("revocation_keys_written") or 0
        )
        job["updated_at"] = _now()
        self.save(job)
        return job

    def pending(self) -> list[dict[str, Any]]:
        if not self.root.exists():
            return []
        jobs: list[dict[str, Any]] = []
        for path in sorted(self.root.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict):
                jobs.append(data)
        return jobs

    def mark_db_deleted(self, job: dict[str, Any], result: Any) -> dict[str, Any]:
        realm_ids = sorted(
            {
                *[str(rid) for rid in job.get("realm_ids", [])],
                *[str(rid) for rid in getattr(result, "realm_ids", [])],
            }
        )
        job["realm_ids"] = realm_ids
        job["db_deleted"] = True
        job["deleted"] = bool(getattr(result, "deleted", False))
        system_counts = owner_cleanup_counts(result)
        runtime_counts = dict(job.get("runtime_counts") or {})
        for key in (
            "runtime_sessions",
            "messages",
            "turns",
            "conversations",
            "jobs",
        ):
            if key in runtime_counts:
                system_counts[key] = int(runtime_counts[key] or 0)
        job["counts"] = system_counts
        job["updated_at"] = _now()
        self.save(job)
        return job

    def complete(self, job: dict[str, Any]) -> None:
        job["memory_purged"] = True
        job["completed_at"] = _now()
        job["updated_at"] = job["completed_at"]
        path = self._path(str(job["job_id"]))
        self.completed_root.mkdir(parents=True, exist_ok=True)
        completed_path = self.completed_root / path.name
        self._write_json(path, job)
        os.replace(path, completed_path)

    def save(self, job: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self._write_json(self._path(str(job["job_id"])), job)

    def _path(self, job_id: str) -> Path:
        return self.root / f"{_safe_id(job_id)}.json"

    @staticmethod
    def _write_json(path: Path, data: dict[str, Any]) -> None:
        tmp_path = path.with_suffix(f".{uuid4().hex}.tmp")
        tmp_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(tmp_path, path)


def _default_journal_dir() -> Path:
    return Path(
        os.environ.get(
            "EIDOLON_OWNER_DELETE_JOURNAL_DIR",
            Path.home() / "eidolon" / "data" / "cleanup-journal" / "owner-delete",
        )
    ).expanduser()


def _default_trash_root() -> Path:
    return Path(
        os.environ.get("EIDOLON_OWNER_DELETE_TRASH_DIR", Path.home() / ".eidolon-trash")
    ).expanduser()


def purge_storage_objects(store: DataStore, storage_keys: list[str]) -> dict[str, Any]:
    """Idempotently delete retained owner objects captured before SQL cascade."""
    deleted: list[str] = []
    errors: list[dict[str, str]] = []
    for storage_key in sorted({key for key in storage_keys if key}):
        try:
            store.object_storage.delete(storage_key)
            deleted.append(storage_key)
        except Exception as exc:  # noqa: BLE001 - journal retries failures
            errors.append({"storage_key": storage_key, "error": str(exc)})
    return {"purged": not errors, "deleted": deleted, "errors": errors}


def _memory_cleanup_complete(memory: dict[str, Any]) -> bool:
    if not bool(memory.get("purged")):
        return False
    return not any("error" in realm for realm in memory.get("realms", []))


def _memory_error_summary(memory: dict[str, Any]) -> str:
    if memory.get("error"):
        return str(memory["error"])
    errors = [
        f"{realm.get('realm_id')}: {realm.get('error')}"
        for realm in memory.get("realms", [])
        if realm.get("error")
    ]
    return "; ".join(errors) or "memory cleanup incomplete"


def _object_error_summary(objects: dict[str, Any]) -> str:
    errors = [
        f"{item.get('storage_key')}: {item.get('error')}"
        for item in objects.get("errors", [])
        if item.get("error")
    ]
    return "; ".join(errors) or "object cleanup incomplete"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_id(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._-")
    return safe or "owner"
