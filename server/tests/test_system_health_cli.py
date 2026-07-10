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
    """If a port is declared unmanaged for its service, a listener does
    NOT trigger orphan refusal — that's expected unsupervised state
    (e.g. vite at :9001).

    Injected via the ``unmanaged`` kwarg (proper DI), so the test
    cannot leak state into other tests — important once pytest-xdist
    runs the suite in parallel.
    """
    port = _pick_free_port()
    cfg = _config_with_port(port)
    proc = _spawn_listener(port)
    try:
        exit_code = await cli.check(
            cleanup=False, verbose=False,
            cfg=cfg,
            unmanaged={"test-svc": frozenset({port})},
        )
        assert exit_code == 0, (
            "unmanaged-by-design port must not trigger orphan refusal"
        )
    finally:
        proc.terminate()
        proc.wait(timeout=5)


@pytest.mark.asyncio
async def test_check_skips_optional_service_port_in_use(capsys) -> None:
    """When a service is marked ``optional: true`` in services.yaml,
    a listener on its declared port is reported as informational but
    does NOT block the cold start. Mirrors the mementos use case:
    operator may be running it as a standalone Electron app outside
    supervisord.
    """
    port = _pick_free_port()
    cfg = GatewayConfig(
        admin=AdminBindConfig(host="127.0.0.1", port=9000, cors_origins=[]),
        services=[
            ServiceConfig(
                id="mementos-like",
                name="Side Project",
                integration="process",
                optional=True,
                auth=AuthConfig(type="none"),
                ports=PortsDecl(declared=[port]),
            ),
        ],
    )
    proc = _spawn_listener(port)
    try:
        exit_code = await cli.check(cleanup=False, verbose=False, cfg=cfg)
        assert exit_code == 0, (
            "optional-service port-bound must not refuse the cold start"
        )
        out = capsys.readouterr().out
        assert "optional service listener" in out, (
            "should emit informational notice about the skipped service"
        )
        assert "mementos-like" in out
        assert "pre-flight passed" in out
    finally:
        proc.terminate()
        proc.wait(timeout=5)


@pytest.mark.asyncio
async def test_check_mixed_optional_and_required_only_blocks_on_required(
    capsys,
) -> None:
    """If BOTH an optional and a required service have busy ports,
    the required-one's blocking takes precedence and we exit 1.
    The optional service is still mentioned in the informational
    block so the operator sees the full picture."""
    opt_port = _pick_free_port()
    req_port = _pick_free_port()
    cfg = GatewayConfig(
        admin=AdminBindConfig(host="127.0.0.1", port=9000, cors_origins=[]),
        services=[
            ServiceConfig(
                id="optional-svc",
                name="Optional",
                integration="process",
                optional=True,
                auth=AuthConfig(type="none"),
                ports=PortsDecl(declared=[opt_port]),
            ),
            ServiceConfig(
                id="required-svc",
                name="Required",
                integration="native",
                auth=AuthConfig(type="none"),
                ports=PortsDecl(declared=[req_port]),
            ),
        ],
    )
    opt_proc = _spawn_listener(opt_port)
    req_proc = _spawn_listener(req_port)
    try:
        exit_code = await cli.check(cleanup=False, verbose=False, cfg=cfg)
        assert exit_code == 1
        out = capsys.readouterr().out
        # both should show up — optional in info block, required in
        # the blocking-listener table.
        assert "optional-svc" in out
        assert "required-svc" in out
    finally:
        for p in (opt_proc, req_proc):
            p.terminate()
            p.wait(timeout=5)


@pytest.mark.asyncio
async def test_check_emit_skip_list_writes_busy_optional_ids(
    tmp_path,
) -> None:
    """Phase 33.A10: --emit-skip-list writes one service_id per line
    for every busy-but-optional listener. run_all.sh consumes this so
    it can ``supervisorctl stop`` the corresponding programs after
    startup, closing the autostart-vs-already-running race.
    """
    opt_port = _pick_free_port()
    other_port = _pick_free_port()  # free → should NOT appear in list
    cfg = GatewayConfig(
        admin=AdminBindConfig(host="127.0.0.1", port=9000, cors_origins=[]),
        services=[
            ServiceConfig(
                id="busy-optional",
                name="Busy Optional",
                integration="process",
                optional=True,
                auth=AuthConfig(type="none"),
                ports=PortsDecl(declared=[opt_port]),
            ),
            ServiceConfig(
                id="free-optional",
                name="Free Optional",
                integration="process",
                optional=True,
                auth=AuthConfig(type="none"),
                ports=PortsDecl(declared=[other_port]),
            ),
        ],
    )
    proc = _spawn_listener(opt_port)
    skip_file = tmp_path / "skip.txt"
    try:
        exit_code = await cli.check(
            cleanup=False,
            verbose=False,
            cfg=cfg,
            emit_skip_list=str(skip_file),
        )
        assert exit_code == 0
        contents = skip_file.read_text(encoding="utf-8").splitlines()
        assert contents == ["busy-optional"], (
            "only the busy-optional id should appear in the skip list; "
            "free-optional has no listener so nothing to stop"
        )
    finally:
        proc.terminate()
        proc.wait(timeout=5)


@pytest.mark.asyncio
async def test_check_emit_skip_list_is_empty_when_no_optional_busy(
    tmp_path,
) -> None:
    """If no optional services have busy ports, --emit-skip-list still
    writes an empty file. run_all.sh's loop short-circuits on empty so
    this is the contract we want.
    """
    cfg = GatewayConfig(
        admin=AdminBindConfig(host="127.0.0.1", port=9000, cors_origins=[]),
        services=[],
    )
    skip_file = tmp_path / "skip.txt"
    exit_code = await cli.check(
        cleanup=False,
        verbose=False,
        cfg=cfg,
        emit_skip_list=str(skip_file),
    )
    assert exit_code == 0
    assert skip_file.exists()
    assert skip_file.read_text(encoding="utf-8") == ""


@pytest.mark.asyncio
async def test_check_service_filter_ignores_unselected_busy_port() -> None:
    selected_port = _pick_free_port()
    unselected_port = _pick_free_port()
    cfg = GatewayConfig(
        admin=AdminBindConfig(host="127.0.0.1", port=9000, cors_origins=[]),
        services=[
            ServiceConfig(
                id="selected",
                name="Selected",
                integration="native",
                auth=AuthConfig(type="none"),
                ports=PortsDecl(declared=[selected_port]),
            ),
            ServiceConfig(
                id="unselected",
                name="Unselected",
                integration="native",
                auth=AuthConfig(type="none"),
                ports=PortsDecl(declared=[unselected_port]),
            ),
        ],
    )
    proc = _spawn_listener(unselected_port)
    try:
        exit_code = await cli.check(
            cleanup=False,
            verbose=False,
            cfg=cfg,
            service_ids=("selected",),
        )
        assert exit_code == 0
    finally:
        proc.terminate()
        proc.wait(timeout=5)


@pytest.mark.asyncio
async def test_check_service_filter_rejects_unknown_service_id() -> None:
    cfg = _config_with_port(_pick_free_port(), service_id="known")
    with pytest.raises(ValueError, match="unknown service id"):
        await cli.check(
            cleanup=False,
            verbose=False,
            cfg=cfg,
            service_ids=("missing",),
        )


def test_main_service_filter_unknown_returns_two(capsys) -> None:
    exit_code = cli.main(["check", "--services", "definitely-missing"])
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "unknown service id" in captured.err


def test_main_unknown_subcommand_returns_two() -> None:
    """argparse error on missing subcommand exits 2 (the default).

    Quick smoke that ``main`` itself wires argparse correctly without
    needing to exercise the full check pipeline.
    """
    # argparse calls SystemExit(2) on parse errors; main isn't reached.
    with pytest.raises(SystemExit) as e:
        cli.main([])
    assert e.value.code == 2
