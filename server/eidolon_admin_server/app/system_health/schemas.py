"""Pydantic models for /api/system/health.

Three nested shapes, surface up to the operator unchanged:

- ``PortStatus``: one row per declared port — who listens, are they
  one of the supervised programs, do they look like an orphan?
- ``ServiceHealth``: rolls up ports + supervised PIDs for one service.
- ``SystemHealthResponse``: top-level envelope with the orphan list
  callable separately.

These are the wire shapes; the audit logic in ``probe.py`` operates on
dataclasses one layer below and the router maps between the two.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


PortState = Literal[
    "ok",            # supervised program listening (PPID in supervisord tree)
    "wrong_owner",   # something listening, but PPID is NOT in supervisord
    "down",          # nobody listening — expected service is missing
    "unmanaged",     # something listening + we don't expect supervisord to own it
                     # (e.g. admin's vite at :9001 is run by run_all.sh, not supervisord)
]


class PortStatus(BaseModel):
    port: int
    state: PortState
    listener_pid: int | None  # OS pid that has LISTEN on this port, if any
    listener_command: str | None  # truncated command line, for operator triage
    listener_ppid: int | None
    listener_ppid_chain: list[int]  # PPID walk up to root, used to detect orphans
    supervised: bool  # listener_pid is a descendant of supervisord


class OrphanProcess(BaseModel):
    """A process holding one of our declared ports without supervisord
    knowing about it — the exact pattern that breaks subsequent starts."""

    pid: int
    ppid: int
    command: str
    declared_for_service: str   # which service expected this port
    port: int
    age_seconds: int            # how long the process has been alive


class ServiceHealth(BaseModel):
    service_id: str
    service_name: str
    supervised: bool                       # has supervisor: block in services.yaml
    supervisor_pids: list[int]             # current PIDs from supervisorctl
    ports: list[PortStatus]


class SystemHealthResponse(BaseModel):
    supervisord_reachable: bool
    supervisord_pid: int | None
    services: list[ServiceHealth]
    orphans: list[OrphanProcess]


class KillOrphanRequest(BaseModel):
    pid: int
    # Sanity guard: caller must repeat the port they're cleaning up.
    # Prevents accidental "click" actions killing the wrong process if
    # state changed between the operator viewing the list and confirming.
    port: int


class KillOrphanResponse(BaseModel):
    pid: int
    signaled: bool
    error: str | None = None
