"""Read-only view of supervisord process state over its XML-RPC socket.

The status overview and the system health audit read this to show what is
running on a macOS Host. Nothing here starts, stops or restarts a program:
that is ``eidolond``'s job on every Host, and Admin reaches it only through
``app/host_services``. A second writer here would bypass its revision checks
and fight its reconcile.

supervisord exposes a synchronous XML-RPC server over a unix socket. We wrap
every call in ``asyncio.to_thread`` so FastAPI handlers stay non-blocking.

References:
- https://supervisord.org/api.html
- ``supervisor.xmlrpc.SupervisorTransport`` for unix socket transport
"""

from __future__ import annotations

import asyncio
import socket
import xmlrpc.client
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from supervisor.xmlrpc import SupervisorTransport


class SupervisorError(Exception):
    """Raised when supervisord answers with an XML-RPC fault."""


class SupervisorUnavailable(Exception):
    """Raised when the supervisord daemon is unreachable (socket missing/dead)."""


@dataclass
class ProcessInfo:
    """Subset of supervisor.getProcessInfo() we expose to the frontend."""

    name: str
    group: str
    state: int
    statename: str
    pid: int
    start: int
    stop: int
    now: int
    exitstatus: int
    description: str
    spawnerr: str
    logfile: str
    stderr_logfile: str

    @classmethod
    def from_rpc(cls, d: dict[str, Any]) -> "ProcessInfo":
        return cls(
            name=d.get("name", ""),
            group=d.get("group", ""),
            state=int(d.get("state", 0)),
            statename=d.get("statename", "UNKNOWN"),
            pid=int(d.get("pid", 0)),
            start=int(d.get("start", 0)),
            stop=int(d.get("stop", 0)),
            now=int(d.get("now", 0)),
            exitstatus=int(d.get("exitstatus", 0)),
            description=d.get("description", ""),
            spawnerr=d.get("spawnerr", ""),
            logfile=d.get("logfile", ""),
            stderr_logfile=d.get("stderr_logfile", ""),
        )

    @property
    def full_name(self) -> str:
        # supervisor identifies programs as `group:name` (e.g. `memory:memory-supervisor`).
        return f"{self.group}:{self.name}"


class SupervisorClient:
    """Thin read-only XML-RPC client over a unix socket. All methods are async."""

    def __init__(self, socket_path: Path) -> None:
        self._socket_path = Path(socket_path)

    def _server(self) -> xmlrpc.client.ServerProxy:
        if not self._socket_path.exists():
            raise SupervisorUnavailable(
                f"supervisord socket not found: {self._socket_path}. "
                "Is supervisord running?"
            )
        # supervisor.xmlrpc.SupervisorTransport accepts unix:// URLs.
        return xmlrpc.client.ServerProxy(
            "http://localhost",
            transport=SupervisorTransport(None, None, f"unix://{self._socket_path}"),
        )

    async def _call(self, attr: str, *args: Any) -> Any:
        def _sync_call() -> Any:
            try:
                server = self._server()
                # Walk dotted method path: e.g. "supervisor.getAllProcessInfo"
                target: Any = server
                for part in attr.split("."):
                    target = getattr(target, part)
                return target(*args)
            except (FileNotFoundError, ConnectionRefusedError, socket.error) as exc:
                raise SupervisorUnavailable(str(exc)) from exc
            except xmlrpc.client.Fault as exc:
                raise SupervisorError(exc.faultString) from exc

        return await asyncio.to_thread(_sync_call)

    async def get_all_process_info(self) -> list[ProcessInfo]:
        raw = await self._call("supervisor.getAllProcessInfo")
        return [ProcessInfo.from_rpc(d) for d in raw]
