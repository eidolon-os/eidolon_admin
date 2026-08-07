"""Isolated performance diagnostics for Admin-adjacent local state services.

The command creates a temporary Bootstrap database, audit projection and NATS
JetStream server. It never reads the formal Eidolon data directory and reports
environment-specific diagnostics rather than product SLA values.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from statistics import median

import nats
from eidolon_admin_server.audit import AuditIndexSettings, AuditIndexStore
from eidolon_admin_server.audit.jetstream import (
    AuditJetStreamSettings,
    JetStreamAuditIndexer,
)
from eidolon_admin_server.bootstrap.adapters.persistence.sqlite import (
    SQLiteBootstrapStateStore,
)
from eidolon_sdk.biz.audit import AuditEnvelope
from sqlalchemy import text


def _free_loopback_port() -> int:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])
    finally:
        listener.close()


def diagnose_bootstrap_busy_timeout(root: Path) -> dict[str, float | int]:
    path = root / "bootstrap.sqlite3"
    holder = SQLiteBootstrapStateStore(path)
    contender = SQLiteBootstrapStateStore(path)
    holder.open()
    holder.initialize(datetime.now(UTC).isoformat())
    contender.open()
    configured_ms = int(
        contender.connection.execute("PRAGMA busy_timeout").fetchone()[0]
    )
    holder.connection.execute("BEGIN IMMEDIATE")
    started = time.perf_counter()
    try:
        try:
            contender.connection.execute(
                "UPDATE bootstrap_state SET updated_at = updated_at WHERE singleton = 1"
            )
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower():
                raise
        else:
            raise RuntimeError("contended Bootstrap write unexpectedly succeeded")
    finally:
        elapsed_ms = (time.perf_counter() - started) * 1_000
        holder.connection.rollback()
        contender.close()
        holder.close()
    return {
        "configured_busy_timeout_ms": configured_ms,
        "observed_lock_failure_ms": round(elapsed_ms, 2),
    }


async def _wait_for_nats(url: str, process: subprocess.Popen[bytes]) -> None:
    async def _ignore_readiness_error(_exc: Exception) -> None:
        return

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"isolated nats-server exited with {process.returncode}")
        try:
            connection = await nats.connect(
                url,
                connect_timeout=0.2,
                max_reconnect_attempts=0,
                error_cb=_ignore_readiness_error,
            )
        except Exception:  # noqa: BLE001 - bounded readiness polling
            await asyncio.sleep(0.05)
            continue
        await connection.close()
        return
    raise RuntimeError("isolated nats-server did not become ready")


async def diagnose_audit_backpressure(
    root: Path,
    *,
    event_count: int,
    fetch_batch: int,
) -> dict[str, float | int]:
    executable = shutil.which("nats-server")
    if executable is None:
        raise RuntimeError("nats-server is not installed")
    port = _free_loopback_port()
    url = f"nats://127.0.0.1:{port}"
    process = subprocess.Popen(
        (
            executable,
            "-js",
            "-sd",
            str(root / "jetstream"),
            "-a",
            "127.0.0.1",
            "-p",
            str(port),
        ),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    index = AuditIndexStore.open(
        AuditIndexSettings(sqlite_path=str(root / "audit-index.sqlite3"))
    )
    indexer = JetStreamAuditIndexer(
        index,
        AuditJetStreamSettings(
            url=url,
            fetch_batch=fetch_batch,
            fetch_timeout_seconds=0.5,
            max_bytes=32 * 1024 * 1024,
        ),
    )
    publisher = None
    try:
        await _wait_for_nats(url, process)
        await index.init_schema()
        await indexer.connect()
        publisher = await nats.connect(url)
        published_at = datetime.now(UTC)
        started = time.perf_counter()
        for sequence in range(event_count):
            envelope = AuditEnvelope(
                event_id=f"diagnostic-{sequence}",
                producer="eidolon-admin-diagnostic",
                producer_seq=sequence + 1,
                category="governance",
                owner_id="diagnostic-owner",
                subject_type="diagnostic",
                subject_id=str(sequence),
                action="diagnostic.observed",
                occurred_at=published_at,
            )
            await publisher.publish(
                "eidolon.audit.v1.diagnostic",
                envelope.model_dump_json().encode(),
            )
        await publisher.flush()
        publish_ms = (time.perf_counter() - started) * 1_000

        batches: list[int] = []
        consumed = 0
        started = time.perf_counter()
        while consumed < event_count:
            batch = await indexer.consume_once()
            if batch <= 0:
                raise RuntimeError("audit consumer stopped before clearing the backlog")
            batches.append(batch)
            consumed += batch
        drain_ms = (time.perf_counter() - started) * 1_000

        async with index.engine.connect() as connection:
            indexed = int(
                (
                    await connection.execute(text("SELECT COUNT(*) FROM audit_events"))
                ).scalar_one()
            )
        if indexed != event_count or max(batches) > fetch_batch:
            raise RuntimeError("audit projection violated its bounded batch contract")

        read_samples: list[float] = []
        for _ in range(20):
            started = time.perf_counter()
            rows = await index.list_for_owner("diagnostic-owner", limit=200)
            read_samples.append((time.perf_counter() - started) * 1_000)
            if len(rows) != min(200, event_count):
                raise RuntimeError("audit projection read returned an unexpected page")
        sorted_reads = sorted(read_samples)
        p95_index = max(0, int(len(sorted_reads) * 0.95) - 1)
        return {
            "events": event_count,
            "fetch_batch_limit": fetch_batch,
            "max_observed_batch": max(batches),
            "batches": len(batches),
            "publish_ms": round(publish_ms, 2),
            "drain_ms": round(drain_ms, 2),
            "drain_events_per_second": round(event_count / (drain_ms / 1_000), 2),
            "read_200_p50_ms": round(median(read_samples), 2),
            "read_200_p95_ms": round(sorted_reads[p95_index], 2),
        }
    finally:
        if publisher is not None:
            await publisher.close()
        await indexer.close()
        await index.close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


async def run(*, event_count: int, fetch_batch: int) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="eidolon-admin-diagnostic-") as temporary:
        root = Path(temporary)
        return {
            "environment": {
                "python": platform.python_version(),
                "platform": platform.platform(),
            },
            "bootstrap_sqlite": diagnose_bootstrap_busy_timeout(root),
            "audit_projection": await diagnose_audit_backpressure(
                root,
                event_count=event_count,
                fetch_batch=fetch_batch,
            ),
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run isolated local-state performance diagnostics"
    )
    parser.add_argument("--events", type=int, default=2_000)
    parser.add_argument("--fetch-batch", type=int, default=200)
    arguments = parser.parse_args(argv)
    if arguments.events < 1 or arguments.fetch_batch < 1:
        parser.error("--events and --fetch-batch must be positive")
    result = asyncio.run(
        run(event_count=arguments.events, fetch_batch=arguments.fetch_batch)
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
