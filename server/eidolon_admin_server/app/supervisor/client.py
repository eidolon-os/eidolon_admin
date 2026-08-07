"""Async-friendly wrapper around supervisord's XML-RPC interface.

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
    """Raised for supervisor-level errors that should map to HTTP 400/409."""


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
    """Thin XML-RPC client over a unix socket. All methods are async."""

    def __init__(self, socket_path: Path) -> None:
        self._socket_path = Path(socket_path)

    @property
    def socket_path(self) -> Path:
        return self._socket_path

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

    # ---- introspection ------------------------------------------------------

    async def ping(self) -> bool:
        """True if supervisord answers; False on any transport error."""
        try:
            await self._call("supervisor.getAPIVersion")
            return True
        except SupervisorUnavailable:
            return False

    async def get_state(self) -> dict[str, Any]:
        return await self._call("supervisor.getState")

    async def get_all_process_info(self) -> list[ProcessInfo]:
        raw = await self._call("supervisor.getAllProcessInfo")
        return [ProcessInfo.from_rpc(d) for d in raw]

    async def get_process_info(self, name: str) -> ProcessInfo:
        # supervisor expects `group:name`; if caller passes only `name` we try
        # bare form first, then fall back to scanning all processes.
        try:
            raw = await self._call("supervisor.getProcessInfo", name)
            return ProcessInfo.from_rpc(raw)
        except SupervisorError:
            for info in await self.get_all_process_info():
                if info.name == name or info.full_name == name:
                    return info
            raise

    # ---- lifecycle ----------------------------------------------------------

    async def start_process(self, name: str, wait: bool = True) -> bool:
        return bool(await self._call("supervisor.startProcess", name, wait))

    async def stop_process(self, name: str, wait: bool = True) -> bool:
        return bool(await self._call("supervisor.stopProcess", name, wait))

    async def start_process_group(
        self, group: str, wait: bool = True
    ) -> list[dict[str, Any]]:
        return await self._call("supervisor.startProcessGroup", group, wait)

    async def stop_process_group(
        self, group: str, wait: bool = True
    ) -> list[dict[str, Any]]:
        return await self._call("supervisor.stopProcessGroup", group, wait)

    # ---- config reconcile ---------------------------------------------------

    async def reload_config(self) -> list[list[list[str]]]:
        """Re-read config from disk. Returns [[added], [changed], [removed]]."""
        return await self._call("supervisor.reloadConfig")

    async def add_process_group(self, name: str) -> bool:
        return bool(await self._call("supervisor.addProcessGroup", name))

    async def remove_process_group(self, name: str) -> bool:
        return bool(await self._call("supervisor.removeProcessGroup", name))

    async def update(self) -> dict[str, list[str]]:
        """Equivalent to ``supervisorctl update``: apply changes from reloadConfig.

        Reads the on-disk config, then adds/removes/restarts process groups so
        running state matches the new config. Returns a summary of changes.
        """
        # supervisor.reloadConfig() returns [[added, changed, removed]] —
        # a one-element outer list wrapping a 3-list of name lists.
        result = await self.reload_config()
        if result and isinstance(result[0], list) and len(result[0]) == 3:
            added, changed, removed = result[0]
        else:
            added = changed = removed = []
        added_groups = list(added)
        changed_groups = list(changed)
        removed_groups = list(removed)

        for grp in removed_groups:
            try:
                await self.stop_process_group(grp)
            except SupervisorError:
                pass
            try:
                await self.remove_process_group(grp)
            except SupervisorError:
                pass

        for grp in changed_groups:
            try:
                await self.stop_process_group(grp)
            except SupervisorError:
                pass
            try:
                await self.remove_process_group(grp)
            except SupervisorError:
                pass
            await self.add_process_group(grp)

        for grp in added_groups:
            await self.add_process_group(grp)

        return {
            "added": added_groups,
            "changed": changed_groups,
            "removed": removed_groups,
        }

    # ---- logs ---------------------------------------------------------------

    async def tail_process_stdout_log(
        self, name: str, offset: int = 0, length: int = 16384
    ) -> tuple[str, int, bool]:
        # supervisor.tailProcessStdoutLog returns [bytes_str, new_offset, overflow]
        result = await self._call(
            "supervisor.tailProcessStdoutLog", name, offset, length
        )
        return result[0], int(result[1]), bool(result[2])

    async def tail_process_stderr_log(
        self, name: str, offset: int = 0, length: int = 16384
    ) -> tuple[str, int, bool]:
        result = await self._call(
            "supervisor.tailProcessStderrLog", name, offset, length
        )
        return result[0], int(result[1]), bool(result[2])
