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
    unmanaged: dict[str, frozenset[int] | set[int]] | None = None,
    emit_skip_list: str | None = None,
) -> int:
    """Return process exit code: 0 = clean, 1 = orphans found / unfixable.

    Uses the auditor's probe helpers directly. Doesn't talk to
    supervisord — at pre-flight time it likely isn't running. Any
    listener on a declared port that isn't in the ``unmanaged`` set is
    treated as a potential orphan.

    Both ``cfg`` and ``unmanaged`` are injectable for tests so they
    don't need to mutate module-level globals. In production (called
    from ``main``) we fall back to the real services.yaml and the
    module-level ``DEFAULT_UNMANAGED_BY_DESIGN``.
    """
    from .auditor import DEFAULT_UNMANAGED_BY_DESIGN

    if cfg is None:
        cfg = load_gateway_config()
    if unmanaged is None:
        unmanaged = DEFAULT_UNMANAGED_BY_DESIGN  # type: ignore[assignment]

    orphans = _scan_for_orphans(cfg, unmanaged)

    # Phase 32+: split out hits on services flagged ``optional: true``
    # in services.yaml (e.g. mementos — operator may run it outside
    # supervisord). Their port-bound state is reported but doesn't
    # block the cold start; the rest of the stack proceeds.
    optional_ids = {s.id for s in cfg.services if s.optional}
    skippable, blocking = _partition_by_optional(orphans, optional_ids)

    # Phase 33.A10: emit the busy-optional service IDs so run_all.sh can
    # stop their supervisord programs *after* startup, closing the
    # "autostart-vs-already-running" race. We write the list whether we
    # end up passing or failing — even if we refuse the start because of
    # blocking orphans, the operator may want the skip list when
    # diagnosing the next attempt.
    if emit_skip_list:
        skip_ids = sorted({o["service_id"] for o in skippable})
        with open(emit_skip_list, "w", encoding="utf-8") as f:
            for sid in skip_ids:
                f.write(f"{sid}\n")

    if not blocking:
        if skippable:
            _print_optional_skip_notice(skippable)
        print(_color(
            "✓ pre-flight passed — declared ports are free (ready for supervisord cold start)",
            _GREEN,
        ))
        if verbose:
            _print_declared_ports(cfg)
        return 0

    eidolon_like, foreign = _partition_listeners(blocking)
    if skippable:
        _print_optional_skip_notice(skippable)
    _print_listener_table(blocking)

    if not cleanup:
        _print_preflight_refusal(eidolon_like, foreign)
        return 1

    # Cleanup path: SIGTERM Eidolon-looking listeners only, then re-audit.
    cleanup_targets = eidolon_like
    skipped = foreign
    if skipped:
        print()
        print(_color(
            f"Skipping {len(skipped)} non-Eidolon listener(s) (will not SIGTERM):",
            _YELLOW,
        ))
        for o in skipped:
            print(f"  pid {o['pid']:>6}  :{o['port']:<6}  {_color(o['command'][:120], _DIM)}")
    if not cleanup_targets:
        print()
        print(_color(
            "Nothing to clean: only non-Eidolon listeners hold the ports. "
            "Edit config/ports.yaml or stop those services, then retry.",
            _RED,
        ))
        return 1

    print()
    print(_color(
        f"Cleaning up {len(cleanup_targets)} Eidolon-looking listener(s) (SIGTERM)...",
        _YELLOW,
    ))
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
        print(_color("✓ cleanup done — declared ports are free", _GREEN))
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
        print(_color("✓ cleanup done (via SIGKILL) — declared ports are free", _GREEN))
        return 0

    print(_color(
        f"✗ {len(final)} listener(s) still hold ports after SIGKILL — manual intervention needed",
        _RED,
    ))
    return 1


def _partition_listeners(
    orphans: list[dict],
) -> tuple[list[dict], list[dict]]:
    eidolon_like: list[dict] = []
    foreign: list[dict] = []
    for o in orphans:
        if _looks_like_eidolon_stack(o["command"]):
            eidolon_like.append(o)
        else:
            foreign.append(o)
    return eidolon_like, foreign


def _partition_by_optional(
    orphans: list[dict], optional_ids: set[str]
) -> tuple[list[dict], list[dict]]:
    """Split listeners into (skippable, blocking).

    A listener is *skippable* if its service is marked ``optional`` in
    services.yaml — pre-flight reports it as informational and the
    cold start proceeds. Everything else is *blocking*.
    """
    skippable: list[dict] = []
    blocking: list[dict] = []
    for o in orphans:
        if o["service_id"] in optional_ids:
            skippable.append(o)
        else:
            blocking.append(o)
    return skippable, blocking


def _print_optional_skip_notice(skippable: list[dict]) -> None:
    """Tell the operator we noticed a busy optional-service port but
    won't refuse the start.

    Note: in earlier docs this said "supervisord won't try to spawn a
    duplicate" — that was wishful. supervisord with ``autostart=true``
    WILL fire the program on startup; what stops the duplicate spawn
    is run_all.sh consuming ``--emit-skip-list`` and issuing
    ``supervisorctl stop <id>`` after start (Phase 33.A10). The notice
    here is just to make the chain visible to the operator."""
    print()
    print(_color(
        f"i {len(skippable)} optional service listener(s) — "
        "kept as-is, cold start continues:",
        _YELLOW,
    ))
    for o in skippable:
        age = _format_age(probe.process_age_seconds(o["pid"]))
        print(
            f"  pid {o['pid']:>6}  :{o['port']:<6}  age {age:>8}  "
            f"service={o['service_id']}  "
            f"{_color(o['command'][:100], _DIM)}"
        )
    print(_color(
        "  (services.yaml marks these ``optional: true`` — pre-flight "
        "doesn't refuse the start when their ports are bound.)",
        _DIM,
    ))


def _print_listener_table(orphans: list[dict]) -> None:
    print()
    print(_color(
        f"pre-flight: {len(orphans)} process(es) already listening on Eidolon ports",
        _YELLOW,
    ))
    print(_color(
        "  ('start' expects these ports to be free before supervisord launches children)",
        _DIM,
    ))
    for o in orphans:
        age = _format_age(probe.process_age_seconds(o["pid"]))
        kind = "eidolon" if _looks_like_eidolon_stack(o["command"]) else "other"
        print(
            f"  [{kind:<7}] pid {o['pid']:>6}  :{o['port']:<6}  age {age:>8}  "
            f"service={o['service_id']}  {_color(o['command'][:100], _DIM)}"
        )


def _print_preflight_refusal(
    eidolon_like: list[dict],
    foreign: list[dict],
) -> None:
    print()
    print(_color("Cannot run 'start' — ports are not free.", _RED))
    print()
    print("What this means:")
    print(
        "  Pre-flight only checks whether something is bound to each port in "
        + _color("config/services.yaml", _GREEN)
        + ". It does "
        + _color("not", _RED)
        + " inspect supervisord or nginx globally."
    )
    if eidolon_like:
        ports = ", ".join(f":{o['port']}" for o in sorted(eidolon_like, key=lambda x: x["port"]))
        print()
        print(_color(f"  • {len(eidolon_like)} listener(s) look like Eidolon dev stack ({ports})", _YELLOW))
        print("    Often this is a stack already running (manual start, old supervisord,")
        print("    or a previous session). This is not necessarily a port misconfiguration.")
    if foreign:
        ports = ", ".join(f":{o['port']}" for o in sorted(foreign, key=lambda x: x["port"]))
        print()
        print(_color(f"  • {len(foreign)} listener(s) look like non-Eidolon services ({ports})", _YELLOW))
        for o in foreign:
            print(f"      pid {o['pid']}  :{o['port']}  {_color(o['command'][:80], _DIM)}")
        print(
            "    Change "
            + _color("config/ports.yaml", _GREEN)
            + " to avoid that port, or stop that service. "
            + _color("Do not use --force-cleanup", _RED)
            + " on these."
        )
    print()
    print("What to do:")
    print(
        "  "
        + _color("./deploy/dev/run_all.sh status", _GREEN)
        + "  — see whether the stack is already up (no port check)"
    )
    print(
        "  "
        + _color("./deploy/dev/run_all.sh restart", _GREEN)
        + "  — stop via this script, then start fresh (recommended)"
    )
    if eidolon_like:
        print(
            "  "
            + _color("./deploy/dev/run_all.sh start --force-cleanup", _GREEN)
            + "  — SIGTERM Eidolon-looking listeners only, then cold start"
        )
    print(
        "  "
        + _color("lsof -i :<port>", _GREEN)
        + "  — inspect a specific port manually"
    )
    print()
    print(_color("Note:", _YELLOW) + " agent HTTP uses :8180 in config/ports.yaml (not :8080).")
    print("      A conflict with nginx on :8080 does not affect this pre-flight check.")


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


def _scan_for_orphans(
    cfg, unmanaged: dict[str, frozenset[int] | set[int]]
) -> list[dict]:
    """Pure listener enumeration, no supervisord involvement.

    At pre-flight time supervisord isn't running, so ANY listener on a
    declared port that isn't in the ``_UNMANAGED_BY_DESIGN`` set counts
    as an orphan we'd otherwise collide with.
    """
    results: list[dict] = []
    for svc in cfg.services:
        unmanaged_for_svc = unmanaged.get(svc.id, frozenset())
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
    check_p.add_argument(
        "--emit-skip-list",
        metavar="PATH",
        default=None,
        help=(
            "Phase 33.A10: write busy-but-optional service IDs (one per "
            "line) to PATH. run_all.sh consumes this so it can stop the "
            "corresponding supervisord programs *after* startup, "
            "preventing the autostart-vs-already-running race on "
            "services like mementos."
        ),
    )
    args = parser.parse_args(argv)

    if args.cmd == "check":
        return asyncio.run(check(
            cleanup=args.cleanup,
            verbose=args.verbose,
            emit_skip_list=args.emit_skip_list,
        ))
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
