"""Local operator CLI for the bootstrap Unix socket."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from .config import load_bootstrap_settings
from .control import BootstrapControlClient, BootstrapControlError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="eidolon-bootstrapctl")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("health")
    subparsers.add_parser("descriptor")

    dev = subparsers.add_parser("dev")
    dev_subparsers = dev.add_subparsers(dest="dev_command", required=True)
    code = dev_subparsers.add_parser("code")
    code.add_argument("--ttl", type=int, default=None)
    dev_subparsers.add_parser("show")
    return parser


async def _execute(args: argparse.Namespace) -> dict[str, Any]:
    settings = load_bootstrap_settings()
    client = BootstrapControlClient(settings.control_socket)
    if args.command == "health":
        return await client.request("health")
    if args.command == "descriptor":
        return await client.request("descriptor")
    if args.command == "dev" and args.dev_command == "code":
        parameters = {} if args.ttl is None else {"ttl_seconds": args.ttl}
        return await client.request("dev.code", **parameters)
    if args.command == "dev" and args.dev_command == "show":
        return await client.request("dev.show")
    raise AssertionError("argparse accepted an unknown bootstrap command")


def main() -> None:
    args = _parser().parse_args()
    try:
        result = asyncio.run(_execute(args))
    except (BootstrapControlError, ConnectionError, FileNotFoundError, OSError) as exc:
        print(f"bootstrapctl: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    if args.command == "dev" and args.dev_command == "code":
        print(f"Setup code: {result['setup_code']}")
        print(f"Host: {result['host_id']}")
        print(f"Expires: {result['expires_at']}")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
