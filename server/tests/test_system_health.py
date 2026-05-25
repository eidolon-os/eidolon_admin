"""Tests for /api/system/health and the auditor.

Mixed strategy:
- ``probe.py`` helpers are tested against real OS state (this process's
  own pid + spawned subprocesses) — no mocks. psutil reads are too
  low-level to meaningfully mock without testing the mock instead of
  the code.
- ``auditor.SystemHealthAuditor`` is tested with a real
  ``GatewayConfig`` (built in-test) + a stub SupervisorClient that
  returns deterministic process info. The SupervisorClient is a
  simple test double (not a mock library) — single source of truth
  about supervisord state.
- Router HTTP shape goes through FastAPI's ASGITransport.

Tests that need orphan-like state spawn real subprocesses bound to
test-only ports (high range) so we don't fight with the running stack.
"""
from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
from typing import AsyncIterator

import httpx
import pytest

from eidolon_admin_server.app.settings import (
    AdminBindConfig,
    AuthConfig,
    GatewayConfig,
    PortsDecl,
    ServiceConfig,
    SupervisorRef,
)
from eidolon_admin_server.app.supervisor.client import ProcessInfo
from eidolon_admin_server.app.system_health import probe
from eidolon_admin_server.app.system_health.auditor import SystemHealthAuditor


# ---- probe.py — real OS reads, no mocks ---------------------------------


def test_get_process_returns_snapshot_for_current_process() -> None:
    """Sanity: psutil-backed probe sees the test process itself."""
    me = probe.get_process(os.getpid())
    assert me is not None
    assert me.pid == os.getpid()
    assert me.ppid > 0
    assert "python" in me.command.lower() or "pytest" in me.command.lower()


def test_get_process_returns_none_for_dead_pid() -> None:
    # 999999 is essentially guaranteed not to exist on a normal dev box.
    assert probe.get_process(999999) is None


def test_ppid_chain_terminates_at_init() -> None:
    """Walking PPID up always ends at 1 (or 0 on some macOS edge cases)."""
    chain = probe.ppid_chain(os.getpid())
    assert chain[0] == os.getpid()
    assert chain[-1] in (0, 1), f"chain should end at init: {chain}"


def test_is_descendant_of_recognises_self() -> None:
    assert probe.is_descendant_of(os.getpid(), os.getpid()) is True


def test_is_descendant_of_via_ppid() -> None:
    """The test process is a descendant of its own parent."""
    me = probe.get_process(os.getpid())
    assert me is not None
    assert probe.is_descendant_of(os.getpid(), me.ppid) is True


def test_is_descendant_of_rejects_unrelated_root() -> None:
    """A PID that's not in our ancestry is not a descendant."""
    # 999999 is essentially guaranteed not to be an ancestor.
    assert probe.is_descendant_of(os.getpid(), 999999) is False


def test_find_port_listener_returns_none_for_unused_port() -> None:
    # Pick a high random port unlikely to be in use. We bind+unbind
    # to discover one, then close before the probe — so it should be
    # confirmed-free.
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    free_port = s.getsockname()[1]
    s.close()
    # Brief sleep so OS releases TIME_WAIT cleanly on macOS.
    time.sleep(0.05)
    assert probe.find_port_listener(free_port) is None


def test_find_port_listener_finds_real_listener(tmp_path) -> None:
    """Spawn a real subprocess listening on a high port and verify probe
    sees it."""
    port = _pick_free_port()
    # Tiny Python one-liner that listens forever; killed in finally.
    proc = subprocess.Popen(
        [sys.executable, "-c",
         f"import socket,time;s=socket.socket();s.bind(('127.0.0.1',{port}));s.listen(1);"
         "import sys;sys.stdout.write('ready\\n');sys.stdout.flush();"
         "time.sleep(30)"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    try:
        # Wait for ready marker so the listen() syscall has executed.
        assert proc.stdout is not None
        line = proc.stdout.readline()
        assert b"ready" in line
        snap = probe.find_port_listener(port)
        assert snap is not None
        assert snap.pid == proc.pid
    finally:
        proc.terminate()
        proc.wait(timeout=5)


# ---- auditor — uses real OS state + a test-double SupervisorClient ------


class _StubSupervisorClient:
    """Minimal stand-in for SupervisorClient.

    Only ``get_all_process_info`` is exercised by the auditor. We
    return a deterministic list of ``ProcessInfo`` derived from the
    test's setup — no live socket, no live supervisord required.

    Not a mock library: a tiny dedicated class lets the test code be
    explicit about *what* supervisord knows. Easier to read than mock
    side-effects.
    """

    def __init__(self, processes: list[ProcessInfo]) -> None:
        self._processes = processes

    async def get_all_process_info(self) -> list[ProcessInfo]:
        return list(self._processes)


def _pick_free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _make_config(*, port: int, ports_for_admin: list[int] | None = None) -> GatewayConfig:
    return GatewayConfig(
        admin=AdminBindConfig(host="127.0.0.1", port=9000, cors_origins=[]),
        services=[
            ServiceConfig(
                id="admin",
                name="Admin",
                integration="native",
                auth=AuthConfig(type="none"),
                supervisor=SupervisorRef(
                    config_file="x",
                    group="admin",
                    programs=["admin-api"],
                ),
                ports=PortsDecl(declared=ports_for_admin or [port]),
            ),
        ],
    )


@pytest.fixture
def listening_subprocess() -> AsyncIterator[tuple[int, subprocess.Popen]]:
    """Spawn a python subprocess listening on a free port."""
    port = _pick_free_port()
    proc = subprocess.Popen(
        [sys.executable, "-c",
         f"import socket,time;s=socket.socket();s.bind(('127.0.0.1',{port}));s.listen(1);"
         "import sys;sys.stdout.write('ready\\n');sys.stdout.flush();"
         "time.sleep(60)"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    assert proc.stdout is not None
    proc.stdout.readline()  # wait for ready
    yield port, proc
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.mark.asyncio
async def test_audit_marks_supervised_port_as_ok(listening_subprocess) -> None:
    """When the listener's PPID chain contains supervisord's pid, state=ok."""
    port, proc = listening_subprocess
    # Pretend supervisord is our test runner (i.e., os.getppid())
    # and the listener (proc.pid) is a descendant via its actual
    # PPID. probe.ppid_chain(proc.pid) → [proc.pid, our_test_pid, our_ppid, ...].
    # So if we tell the auditor that supervisord's pid is our test pid,
    # the listener (child of test) IS supervised.
    sv_pid = os.getpid()
    cfg = _make_config(port=port)
    stub = _StubSupervisorClient(processes=[
        ProcessInfo(
            name="admin-api", group="admin",
            state=20, statename="RUNNING", pid=proc.pid, start=0, stop=0,
            now=0, exitstatus=0, description="", spawnerr="",
            logfile="", stderr_logfile="",
        ),
    ])

    audit = await SystemHealthAuditor(cfg, stub).audit()  # type: ignore[arg-type]

    assert audit.supervisord_reachable is True
    assert audit.supervisord_pid == sv_pid
    assert len(audit.services) == 1
    svc = audit.services[0]
    assert len(svc.ports) == 1
    port_audit = svc.ports[0]
    assert port_audit.state == "ok"
    assert port_audit.supervised is True
    assert port_audit.listener is not None
    assert port_audit.listener.pid == proc.pid
    assert audit.orphans == []


@pytest.mark.asyncio
async def test_audit_marks_unowned_listener_as_orphan() -> None:
    """A listener whose PPID chain does NOT contain supervisord's pid →
    state=wrong_owner + appears in orphans list.

    We construct this by spawning a subprocess (child of the test
    process) and telling the auditor that supervisord is some
    UNRELATED pid (the listener's not its descendant).
    """
    port = _pick_free_port()
    proc = subprocess.Popen(
        [sys.executable, "-c",
         f"import socket,time;s=socket.socket();s.bind(('127.0.0.1',{port}));s.listen(1);"
         "import sys;sys.stdout.write('ready\\n');sys.stdout.flush();"
         "time.sleep(60)"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    try:
        assert proc.stdout is not None
        proc.stdout.readline()

        cfg = _make_config(port=port)
        # supervisord 'pid' = 1 (init). Listener is a child of our test
        # process (not init's direct descendant via the chain we care
        # about), so it should NOT be classified as supervised.
        stub = _StubSupervisorClient(processes=[
            ProcessInfo(
                name="admin-api", group="admin",
                state=20, statename="RUNNING", pid=99999999,
                start=0, stop=0, now=0, exitstatus=0, description="",
                spawnerr="", logfile="", stderr_logfile="",
            ),
        ])
        audit = await SystemHealthAuditor(cfg, stub).audit()  # type: ignore[arg-type]
    finally:
        proc.terminate()
        proc.wait(timeout=5)

    # supervisord_pid will be None because the program PID we fed (99999999)
    # doesn't exist — _supervisord_snapshot can't derive supervisord_pid
    # from a non-existent program. With supervisord_pid=None, every
    # listener is classified wrong_owner.
    assert audit.supervisord_pid is None
    assert len(audit.orphans) == 1
    o = audit.orphans[0]
    assert o.pid == proc.pid
    assert o.port == port
    assert o.declared_for_service == "admin"
    assert o.age_seconds >= 0


@pytest.mark.asyncio
async def test_audit_classifies_unmanaged_admin_9001_correctly() -> None:
    """The synthetic ``admin`` service's vite port (9001) is in the
    auditor's ``_UNMANAGED_BY_DESIGN`` set. If a listener is present
    but not under supervisord, classify as 'unmanaged' rather than
    'wrong_owner' — vite is meant to be run by run_all.sh.
    """
    port = _pick_free_port()
    proc = subprocess.Popen(
        [sys.executable, "-c",
         f"import socket,time;s=socket.socket();s.bind(('127.0.0.1',{port}));s.listen(1);"
         "import sys;sys.stdout.write('ready\\n');sys.stdout.flush();"
         "time.sleep(60)"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    try:
        assert proc.stdout is not None
        proc.stdout.readline()

        cfg = _make_config(port=port, ports_for_admin=[port])
        # Add port to UNMANAGED_BY_DESIGN at runtime — test-scoped
        # via the class attribute, not a monkeypatch of behaviour.
        orig = SystemHealthAuditor._UNMANAGED_BY_DESIGN.get("admin", set()).copy()
        SystemHealthAuditor._UNMANAGED_BY_DESIGN["admin"] = {port}
        try:
            stub = _StubSupervisorClient(processes=[])  # no supervised programs at all
            audit = await SystemHealthAuditor(cfg, stub).audit()  # type: ignore[arg-type]
        finally:
            SystemHealthAuditor._UNMANAGED_BY_DESIGN["admin"] = orig

        svc = audit.services[0]
        assert svc.ports[0].state == "unmanaged"
        assert audit.orphans == [], (
            "unmanaged ports must NOT appear in orphans — they're expected to be unsupervised"
        )
    finally:
        proc.terminate()
        proc.wait(timeout=5)


@pytest.mark.asyncio
async def test_audit_reports_down_for_unused_declared_port() -> None:
    """No listener on a declared port → state=down + nothing in orphans."""
    port = _pick_free_port()
    # No subprocess — port is just free.
    cfg = _make_config(port=port)
    stub = _StubSupervisorClient(processes=[])
    audit = await SystemHealthAuditor(cfg, stub).audit()  # type: ignore[arg-type]
    svc = audit.services[0]
    assert svc.ports[0].state == "down"
    assert svc.ports[0].listener is None


@pytest.mark.asyncio
async def test_kill_orphan_with_mismatched_port_refuses() -> None:
    """Sanity guard: if pid → port disagrees with the audit, the kill
    refuses. Prevents racing with state changes between the operator
    seeing the list and clicking the button."""
    port = _pick_free_port()
    # Don't actually spawn anything on that port.
    cfg = _make_config(port=port)
    stub = _StubSupervisorClient(processes=[])
    ok, err = await SystemHealthAuditor(cfg, stub).kill_orphan(  # type: ignore[arg-type]
        pid=99999999, expected_port=port,
    )
    assert ok is False
    assert err is not None and "not the listener" in err.lower()


# ---- router HTTP shape --------------------------------------------------


@pytest.mark.asyncio
async def test_router_returns_audit_envelope() -> None:
    """Sanity that the router successfully maps auditor dataclasses
    onto the documented Pydantic envelope."""
    from eidolon_admin_server.app.main import create_app

    cfg = GatewayConfig(
        admin=AdminBindConfig(host="127.0.0.1", port=9000, cors_origins=[]),
        services=[
            ServiceConfig(
                id="x", name="X",
                integration="native",
                auth=AuthConfig(type="none"),
                ports=PortsDecl(declared=[_pick_free_port()]),
            ),
        ],
    )
    app = create_app(cfg)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", trust_env=False,
    ) as client:
        resp = await client.get("/api/system/health")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "services" in body
        assert "orphans" in body
        assert isinstance(body["supervisord_reachable"], bool)
    await asyncio.sleep(0)  # let the ASGI transport drain cleanly
