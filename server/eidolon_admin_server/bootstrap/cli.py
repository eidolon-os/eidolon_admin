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

    reset = subparsers.add_parser(
        "controller-reset",
        help=(
            "revoke every Controller Grant and open one bounded window in which "
            "any phone may claim this Host again; keeps the Host identity, "
            "Owner, network and all component data"
        ),
    )
    reset.add_argument("--ttl", type=int, default=None)
    code = subparsers.add_parser(
        "commissioning-code",
        help=(
            "mint the one-time Setup code a phone types to claim this Host; "
            "reaching this socket is already the Host's own root authority"
        ),
    )
    code.add_argument("--ttl", type=int, default=None)
    dev = subparsers.add_parser("dev")
    dev_subparsers = dev.add_subparsers(dest="dev_command", required=True)
    dev_subparsers.add_parser("show")
    reset = dev_subparsers.add_parser("reset")
    reset.add_argument(
        "--forget-wifi",
        action="store_true",
        help="delete every saved Wi-Fi profile and disconnect the Host",
    )
    return parser


async def _execute(args: argparse.Namespace) -> dict[str, Any]:
    settings = load_bootstrap_settings()
    client = BootstrapControlClient(settings.control_socket)
    if args.command == "health":
        return await client.request("health")
    if args.command == "descriptor":
        return await client.request("descriptor")
    if args.command == "controller-reset":
        parameters = {} if args.ttl is None else {"ttl_seconds": args.ttl}
        return await client.request("controller.reset", **parameters)
    if args.command == "commissioning-code":
        parameters = {} if args.ttl is None else {"ttl_seconds": args.ttl}
        return await client.request("commissioning.code", **parameters)
    if args.command == "dev" and args.dev_command == "show":
        return await client.request("dev.show")
    if args.command == "dev" and args.dev_command == "reset":
        return await client.request(
            "dev.reset",
            forget_wifi_profiles=args.forget_wifi,
        )
    raise AssertionError("argparse accepted an unknown bootstrap command")


def main() -> None:
    args = _parser().parse_args()
    try:
        result = asyncio.run(_execute(args))
    except (BootstrapControlError, ConnectionError, FileNotFoundError, OSError) as exc:
        print(f"bootstrapctl: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    if args.command == "commissioning-code":
        # The code is half of the act; the session that accepts it is the
        # other half, and a phone off the LAN is told only the second.
        print(f"Setup code: {result['setup_code']}")
        print(f"Host: {result['host_id']}")
        print(f"Commissioning: {result['commissioning_id']}")
        print(f"Expires: {result['expires_at']}")
    else:
        # controller-reset stays one JSON document and nothing else: the host
        # agent that wraps it parses stdout, and a friendly line above the
        # document would make the operator's recovery command fail to parse.
        # The Setup code travels inside it, under "setup_session".
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
