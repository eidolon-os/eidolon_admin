"""CLI for the independent global audit query projection."""

from __future__ import annotations

import argparse
import asyncio
import os

from .index import AuditIndexSettings, AuditIndexStore
from .jetstream import AuditJetStreamSettings, JetStreamAuditIndexer


async def _run(args: argparse.Namespace) -> None:
    index = AuditIndexStore.open(AuditIndexSettings(sqlite_path=args.sqlite_path))
    await index.init_schema()
    worker = JetStreamAuditIndexer(
        index,
        AuditJetStreamSettings(
            url=args.nats_url,
            stream=args.stream,
            subject_prefix=args.subject_prefix,
            durable_consumer=args.durable,
            fetch_batch=args.batch_size,
        ),
    )
    try:
        while True:
            await worker.consume_once()
    finally:
        await worker.close()
        await index.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build the Eidolon global audit index")
    parser.add_argument(
        "--sqlite-path",
        default=os.path.expanduser("~/eidolon/data/audit-index.sqlite3"),
    )
    parser.add_argument(
        "--nats-url",
        default=os.environ.get("EIDOLON_NATS_URL", "nats://127.0.0.1:4222"),
    )
    parser.add_argument("--stream", default="EIDOLON_AUDIT_V1")
    parser.add_argument("--subject-prefix", default="eidolon.audit.v1")
    parser.add_argument("--durable", default="eidolon-audit-indexer-v1")
    parser.add_argument("--batch-size", type=int, default=200)
    return parser


def main() -> None:
    asyncio.run(_run(_parser().parse_args()))


if __name__ == "__main__":
    main()
