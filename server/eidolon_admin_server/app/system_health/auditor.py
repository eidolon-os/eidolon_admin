"""System health orchestration: cross-reference services.yaml ↔ supervisord
↔ live OS state.

This is the only module that knows three things at once:

  - services.yaml's ``ports.declared`` for each service (the *contract*)
  - supervisord's current PID set (the *expected* runtime)
  - what's actually listening at the OS level (the *real* runtime)

For each declared port it produces a verdict in {ok, wrong_owner, down,
unmanaged}. Anything in ``wrong_owner`` becomes an "orphan" candidate
the operator can decide to kill from the UI.

Why not let supervisor router do this:
    Supervisor router knows about programs, not about the services.yaml
    port contract or OS-level ports. Mixing the two concerns there
    would force every supervisor caller to also reason about port
    state, which is overkill 95% of the time.
"""
from __future__ import annotations

import logging
import signal
from dataclasses import dataclass

from ..settings import GatewayConfig, ServiceConfig
from ..supervisor.client import SupervisorClient, SupervisorError, SupervisorUnavailable
from . import probe
from .probe import ProcessSnapshot

logger = logging.getLogger(__name__)


# ---- view models (orchestrator returns these; router maps to Pydantic) ----


@dataclass
class PortAudit:
    port: int
    state: str  # "ok" | "wrong_owner" | "down" | "unmanaged"
    listener: ProcessSnapshot | None
    listener_ppid_chain: list[int]
    supervised: bool


@dataclass
class ServiceAudit:
    service_id: str
    service_name: str
    supervised: bool
    supervisor_pids: list[int]
    ports: list[PortAudit]


@dataclass
class OrphanAudit:
    pid: int
    ppid: int
    command: str
    declared_for_service: str
    port: int
    age_seconds: int


@dataclass
class SystemHealthAudit:
    supervisord_reachable: bool
    supervisord_pid: int | None
    services: list[ServiceAudit]
    orphans: list[OrphanAudit]


# ---- the orchestrator ----------------------------------------------------


class SystemHealthAuditor:
    """Single shot ``audit()`` returns the whole picture.

    Stateless across calls — each invocation reads fresh OS + supervisord
    state. Callers that want polling just call it repeatedly; we don't
    cache because the answer is meant to be a *real-time* view.
    """

    # Services where supervised=False is legitimate (vite for admin,
    # not really a "service" but it's in services.yaml and has a port).
    # For these, ports listening with PPID NOT in supervisord's child
    # set are classified as "unmanaged" instead of "wrong_owner".
    _UNMANAGED_BY_DESIGN: dict[str, set[int]] = {
        "admin": {9001},  # vite dev server is launched by run_all.sh
    }

    def __init__(
        self,
        gateway_config: GatewayConfig,
        supervisor_client: SupervisorClient,
    ) -> None:
        self._cfg = gateway_config
        self._sv = supervisor_client

    async def audit(self) -> SystemHealthAudit:
        sv_pid, sv_program_pids, sv_reachable = await self._supervisord_snapshot()

        services_out: list[ServiceAudit] = []
        orphans_out: list[OrphanAudit] = []

        for svc in self._cfg.services:
            port_audits, service_orphans = self._audit_service_ports(
                svc, sv_pid, sv_program_pids,
            )
            services_out.append(ServiceAudit(
                service_id=svc.id,
                service_name=svc.name,
                supervised=svc.supervisor is not None,
                supervisor_pids=self._pids_for_service(svc, sv_program_pids),
                ports=port_audits,
            ))
            orphans_out.extend(service_orphans)

        return SystemHealthAudit(
            supervisord_reachable=sv_reachable,
            supervisord_pid=sv_pid,
            services=services_out,
            orphans=orphans_out,
        )

    async def kill_orphan(self, pid: int, expected_port: int) -> tuple[bool, str | None]:
        """Operator-confirmed orphan kill. Two-stage TERM-then-KILL.

        ``expected_port`` is a sanity guard: the orphan listed in the
        audit response was tied to a specific port. If state changes
        between operator viewing + clicking, the new process at this
        pid might be different — refuse if it doesn't match.
        """
        live = probe.find_port_listener(expected_port)
        if live is None or live.pid != pid:
            return False, (
                f"pid {pid} is not the listener for port {expected_port} "
                "anymore — refresh the audit and try again"
            )
        # SIGTERM first; psutil + send_signal allow the process to clean
        # up. The operator can re-trigger if SIGTERM is ignored — we
        # don't auto-escalate to SIGKILL because losing data on a
        # mis-identified process is worse than the operator clicking
        # twice.
        ok, err = probe.send_signal(pid, signal.SIGTERM)
        return ok, err

    # ---- internals ------------------------------------------------------

    async def _supervisord_snapshot(self) -> tuple[int | None, dict[str, int], bool]:
        """Return (supervisord_pid, {full_name: pid}, reachable_bool)."""
        try:
            infos = await self._sv.get_all_process_info()
        except (SupervisorUnavailable, SupervisorError) as exc:
            logger.warning("supervisord unreachable during audit: %s", exc)
            return None, {}, False

        # supervisord's own pid: get_all_process_info doesn't return it
        # directly but we read it from the var/supervisord.pid path
        # through psutil — easier to just inspect the supervisor library
        # for it once we have at least one program PID, walk up the
        # PPID chain to find the common ancestor.
        sv_pid: int | None = None
        program_pids: dict[str, int] = {}
        for info in infos:
            if info.pid and info.pid > 0:
                program_pids[info.full_name] = info.pid
        if program_pids:
            # Take any program's PPID; that IS supervisord.
            any_program_pid = next(iter(program_pids.values()))
            snap = probe.get_process(any_program_pid)
            if snap is not None:
                sv_pid = snap.ppid

        return sv_pid, program_pids, True

    def _pids_for_service(
        self, svc: ServiceConfig, sv_program_pids: dict[str, int],
    ) -> list[int]:
        if svc.supervisor is None:
            return []
        group = svc.supervisor.group or ""
        pids: list[int] = []
        for prog_name in svc.supervisor.programs:
            full = f"{group}:{prog_name}" if group else prog_name
            pid = sv_program_pids.get(full)
            if pid:
                pids.append(pid)
        return pids

    def _audit_service_ports(
        self,
        svc: ServiceConfig,
        sv_pid: int | None,
        sv_program_pids: dict[str, int],
    ) -> tuple[list[PortAudit], list[OrphanAudit]]:
        port_audits: list[PortAudit] = []
        orphans: list[OrphanAudit] = []
        unmanaged_ports = self._UNMANAGED_BY_DESIGN.get(svc.id, set())

        for port in svc.ports.declared:
            listener = probe.find_port_listener(port)

            if listener is None:
                port_audits.append(PortAudit(
                    port=port,
                    state="down",
                    listener=None,
                    listener_ppid_chain=[],
                    supervised=False,
                ))
                continue

            chain = probe.ppid_chain(listener.pid)
            supervised = sv_pid is not None and sv_pid in chain
            if supervised:
                state = "ok"
            elif port in unmanaged_ports:
                state = "unmanaged"
            else:
                state = "wrong_owner"
                orphans.append(OrphanAudit(
                    pid=listener.pid,
                    ppid=listener.ppid,
                    command=listener.command,
                    declared_for_service=svc.id,
                    port=port,
                    age_seconds=probe.process_age_seconds(listener.pid),
                ))

            port_audits.append(PortAudit(
                port=port,
                state=state,
                listener=listener,
                listener_ppid_chain=chain,
                supervised=supervised,
            ))

        return port_audits, orphans
