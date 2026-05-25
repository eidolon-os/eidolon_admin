"""Tests for the pre-flight port audit CLI.

Strategy:
- Spawn a real subprocess listening on a free port declared in a
  test-only ``GatewayConfig`` we build inline.
- Inject the config into ``cli.check`` via its ``cfg`` parameter
  (proper DI, not monkey-patching the module's loader).
- Verify: exit 0 when clean, exit 1 when orphan present, exit 0 when
  ``--cleanup`` successfully kills the orphan.

No mocks: real processes, real psutil, real signals.
"""
from __future__ import annotations

import socket
import subprocess
import sys

import pytest

from eidolon_admin_server.app.settings import (
    AdminBindConfig,
    AuthConfig,
    GatewayConfig,
    PortsDecl,
    ServiceConfig,
)
from eidolon_admin_server.app.system_health import cli


def _pick_free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _spawn_listener(port: int) -> subprocess.Popen:
    proc = subprocess.Popen(
        [sys.executable, "-c",
         f"import socket,time;s=socket.socket();s.bind(('127.0.0.1',{port}));s.listen(1);"
         "import sys;sys.stdout.write('ready\\n');sys.stdout.flush();"
         "time.sleep(60)"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    assert proc.stdout is not None
    proc.stdout.readline()  # wait for ready marker
    return proc


def _config_with_port(port: int, service_id: str = "test-svc") -> GatewayConfig:
    """Inline test-only GatewayConfig with one service declaring ``port``."""
    return GatewayConfig(
        admin=AdminBindConfig(host="127.0.0.1", port=9000, cors_origins=[]),
        services=[
            ServiceConfig(
                id=service_id,
                name="Test Service",
                integration="native",
                auth=AuthConfig(type="none"),
                ports=PortsDecl(declared=[port]),
            ),
        ],
    )


@pytest.mark.asyncio
async def test_check_returns_zero_when_no_listeners() -> None:
    """Pre-flight on an unused port → exit 0."""
    cfg = _config_with_port(_pick_free_port())
    exit_code = await cli.check(cleanup=False, verbose=False, cfg=cfg)
    assert exit_code == 0


@pytest.mark.asyncio
async def test_check_returns_one_when_orphan_present() -> None:
    """A listener on the declared port without --cleanup → exit 1."""
    port = _pick_free_port()
    cfg = _config_with_port(port)
    proc = _spawn_listener(port)
    try:
        exit_code = await cli.check(cleanup=False, verbose=False, cfg=cfg)
        assert exit_code == 1
    finally:
        proc.terminate()
        proc.wait(timeout=5)


@pytest.mark.asyncio
async def test_check_with_cleanup_kills_orphan_and_returns_zero() -> None:
    """--cleanup SIGTERMs the orphan and re-audits; should exit 0."""
    port = _pick_free_port()
    cfg = _config_with_port(port)
    proc = _spawn_listener(port)
    try:
        exit_code = await cli.check(cleanup=True, verbose=False, cfg=cfg)
        assert exit_code == 0
        # Subprocess should be gone (CLI's send_signal landed).
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pytest.fail("--cleanup should have killed the orphan")
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=5)


@pytest.mark.asyncio
async def test_check_skips_unmanaged_ports() -> None:
    """If a port is in ``_UNMANAGED_BY_DESIGN`` for its service, a
    listener does NOT trigger orphan refusal — that's expected
    unsupervised state (e.g. vite at :9001).

    The unmanaged set IS module-level config (single source of truth
    about which ports are admin-by-design unsupervised); mutating it
    in a test is changing data, not monkey-patching behaviour. We
    restore on teardown via ``finally``.
    """
    from eidolon_admin_server.app.system_health.auditor import SystemHealthAuditor

    port = _pick_free_port()
    cfg = _config_with_port(port)
    proc = _spawn_listener(port)
    try:
        orig = SystemHealthAuditor._UNMANAGED_BY_DESIGN.get("test-svc", set()).copy()
        SystemHealthAuditor._UNMANAGED_BY_DESIGN["test-svc"] = {port}
        try:
            exit_code = await cli.check(cleanup=False, verbose=False, cfg=cfg)
            assert exit_code == 0, (
                "unmanaged-by-design port must not trigger orphan refusal"
            )
        finally:
            SystemHealthAuditor._UNMANAGED_BY_DESIGN["test-svc"] = orig
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_main_unknown_subcommand_returns_two() -> None:
    """argparse error on missing subcommand exits 2 (the default).

    Quick smoke that ``main`` itself wires argparse correctly without
    needing to exercise the full check pipeline.
    """
    # argparse calls SystemExit(2) on parse errors; main isn't reached.
    with pytest.raises(SystemExit) as e:
        cli.main([])
    assert e.value.code == 2
