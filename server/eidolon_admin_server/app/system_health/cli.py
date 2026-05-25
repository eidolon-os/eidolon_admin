"""Pre-flight port audit CLI — invoked by run_all.sh before starting supervisord.

Why a separate entry point (not just curl /api/system/health):
    At pre-flight, supervisord isn't running yet — so admin-api isn't
    running, so there IS no HTTP endpoint to curl. We share the
    services.yaml + probe code with the runtime endpoint and skip the
    HTTP layer entirely.

Usage:
    python -m eidolon_admin_server.app.system_health.cli check
        Exit 0 if no orphans, 1 if orphans found. Prints a summary.

    python -m eidolon_admin_server.app.system_health.cli check --cleanup
        Same check, but SIGTERM any orphans found and re-audit. If
        the second pass still shows orphans (kills didn't take), exit 1.

The auditor's normal classification still applies: ports that should be
unmanaged (vite at 9001) are not flagged as orphans.
"""
from __future__ import annotations

import argparse
import asyncio
import signal
import sys
import time

from ..settings import GatewayConfig, load_gateway_config
from . import probe


_GREEN = "\033[0;32m"
_YELLOW = "\033[1;33m"
_RED = "\033[0;31m"
_DIM = "\033[2m"
_NC = "\033[0m"


def _color(s: str, code: str) -> str:
    if not sys.stdout.isatty():
        return s
    return f"{code}{s}{_NC}"


async def check(
    *,
    cleanup: bool,
    verbose: bool,
    cfg: GatewayConfig | None = None,
) -> int:
    """Return process exit code: 0 = clean, 1 = orphans found / unfixable.

    Uses the auditor's probe helpers directly. Doesn't talk to
    supervisord — at pre-flight time it likely isn't running. Any
    listener on a declared port that isn't in the auditor's
    ``_UNMANAGED_BY_DESIGN`` set is treated as a potential orphan.

    ``cfg`` is injectable for tests so they don't need to point at the
    real services.yaml. In production (called from ``main``) we load
    the on-disk config; this DI keeps the testable path explicit
    instead of monkey-patching the module-level loader.
    """
    if cfg is None:
        cfg = load_gateway_config()
    from .auditor import SystemHealthAuditor

    unmanaged = SystemHealthAuditor._UNMANAGED_BY_DESIGN

    orphans = _scan_for_orphans(cfg, unmanaged)

    if not orphans:
        print(_color("✓ pre-flight clean — no orphan processes on declared ports", _GREEN))
        if verbose:
            _print_declared_ports(cfg)
        return 0

    print(_color(f"⚠ pre-flight: {len(orphans)} orphan process(es) on declared ports", _YELLOW))
    for o in orphans:
        age = _format_age(probe.process_age_seconds(o["pid"]))
        print(
            f"  pid {o['pid']:>6}  :{o['port']:<6}  age {age:>8}  "
            f"({o['service_id']})  {_color(o['command'][:120], _DIM)}"
        )

    if not cleanup:
        print()
        print(_color(
            "Refusing to start: orphans hold ports admin's children will need.",
            _RED,
        ))
        foreign = [o for o in orphans if not _looks_like_eidolon_stack(o["command"])]
        if foreign:
            print()
            print(_color(
                "Some listeners look like unrelated system services (e.g. nginx on :8080).",
                _YELLOW,
            ))
            print(
                "Do " + _color("not", _RED) + " use --cleanup on those — change "
                + _color("config/ports.yaml", _GREEN)
                + " or stop the other service instead."
            )
        print()
        print(
            "Re-run with " + _color("--cleanup", _GREEN)
            + " to SIGTERM stale Eidolon orphans only,"
        )
        print("or inspect with " + _color("lsof -i :<port>", _GREEN)
              + " and kill manually.")
        return 1

    # Cleanup path: SIGTERM each orphan, give them a moment to die, re-audit.
    # Skip listeners that look like unrelated system daemons (nginx, etc.).
    cleanup_targets = [o for o in orphans if _looks_like_eidolon_stack(o["command"])]
    skipped = [o for o in orphans if o not in cleanup_targets]
    if skipped:
        print()
        print(_color(
            f"Skipping cleanup for {len(skipped)} non-Eidolon listener(s):",
            _YELLOW,
        ))
        for o in skipped:
            print(f"  pid {o['pid']:>6}  :{o['port']:<6}  {_color(o['command'][:120], _DIM)}")
    if not cleanup_targets:
        print()
        print(_color(
            "No Eidolon orphans to clean — fix port conflicts in config/ports.yaml "
            "or stop the other service(s).",
            _RED,
        ))
        return 1

    print()
    print(_color("Cleaning up orphans (SIGTERM)...", _YELLOW))
    for o in cleanup_targets:
        ok, err = probe.send_signal(o["pid"], signal.SIGTERM)
        if ok:
            print(f"  pid {o['pid']:>6}  ✓ SIGTERM sent")
        else:
            print(f"  pid {o['pid']:>6}  ✗ {err}")

    # Give the kernel a beat to free the ports.
    time.sleep(1.5)

    survivors = _scan_for_orphans(cfg, unmanaged)
    if not survivors:
        print(_color("✓ all orphans cleaned, pre-flight ready", _GREEN))
        return 0

    # Escalate to SIGKILL on stragglers.
    print(_color(f"⚠ {len(survivors)} survivor(s) after SIGTERM; escalating to SIGKILL", _YELLOW))
    for o in survivors:
        ok, err = probe.send_signal(o["pid"], signal.SIGKILL)
        if ok:
            print(f"  pid {o['pid']:>6}  ✓ SIGKILL sent")
        else:
            print(f"  pid {o['pid']:>6}  ✗ {err}")
    time.sleep(1.0)

    final = _scan_for_orphans(cfg, unmanaged)
    if not final:
        print(_color("✓ all orphans cleaned (via SIGKILL), pre-flight ready", _GREEN))
        return 0

    print(_color(f"✗ {len(final)} orphan(s) survived even SIGKILL — manual intervention needed", _RED))
    return 1


def _looks_like_eidolon_stack(command: str) -> bool:
    """Heuristic: is this listener probably a leftover from our dev stack?"""
    cmd = command.lower()
    markers = (
        "eidolon",
        "uvicorn",
        "supervisord",
        "nats-server",
        "livekit-server",
        "vite",
        "next dev",
        "node_modules/.bin/next",
        "eidolon-memory",
        "eidolon-agent",
    )
    return any(m in cmd for m in markers)


def _scan_for_orphans(cfg, unmanaged: dict[str, set[int]]) -> list[dict]:
    """Pure listener enumeration, no supervisord involvement.

    At pre-flight time supervisord isn't running, so ANY listener on a
    declared port that isn't in the ``_UNMANAGED_BY_DESIGN`` set counts
    as an orphan we'd otherwise collide with.
    """
    results: list[dict] = []
    for svc in cfg.services:
        unmanaged_for_svc = unmanaged.get(svc.id, set())
        for port in svc.ports.declared:
            if port in unmanaged_for_svc:
                continue
            listener = probe.find_port_listener(port)
            if listener is None:
                continue
            results.append({
                "pid": listener.pid,
                "ppid": listener.ppid,
                "command": listener.command,
                "port": port,
                "service_id": svc.id,
            })
    return results


def _print_declared_ports(cfg) -> None:
    print()
    print("Declared ports (services.yaml):")
    for svc in cfg.services:
        if svc.ports.declared:
            ports = ", ".join(str(p) for p in svc.ports.declared)
            print(f"  {svc.id:<12} {ports}")


def _format_age(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    h, rem = divmod(seconds, 3600)
    return f"{h}h{rem // 60}m"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="port_audit",
        description="Pre-flight port audit for the Eidolon dev stack.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    check_p = sub.add_parser("check", help="audit declared ports for orphans")
    check_p.add_argument(
        "--cleanup", action="store_true",
        help="SIGTERM any orphans found and re-audit",
    )
    check_p.add_argument(
        "-v", "--verbose", action="store_true",
        help="also print the declared-ports list on success",
    )
    args = parser.parse_args(argv)

    if args.cmd == "check":
        return asyncio.run(check(cleanup=args.cleanup, verbose=args.verbose))
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
