"""Filesystem-protected Unix socket control plane for bootstrapd."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import stat
from pathlib import Path
from typing import Any

from .service import BootstrapOperationRejected, BootstrapService


_MAX_REQUEST_BYTES = 64 * 1024
logger = logging.getLogger("eidolon.bootstrap.control")


class BootstrapControlError(RuntimeError):
    """A structured error returned by the local control socket."""


class BootstrapControlServer:
    def __init__(self, socket_path: Path, service: BootstrapService) -> None:
        self._socket_path = socket_path
        self._service = service
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        self._socket_path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
        if self._socket_path.exists():
            path_stat = self._socket_path.lstat()
            if not stat.S_ISSOCK(path_stat.st_mode):
                raise BootstrapControlError(
                    f"refusing to replace non-socket path: {self._socket_path}"
                )
            self._socket_path.unlink()
        self._server = await asyncio.start_unix_server(
            self._handle_client,
            path=self._socket_path,
            limit=_MAX_REQUEST_BYTES,
        )
        os.chmod(self._socket_path, 0o660)

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        if self._socket_path.exists() and stat.S_ISSOCK(
            self._socket_path.lstat().st_mode
        ):
            self._socket_path.unlink()

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            raw = await asyncio.wait_for(reader.readline(), timeout=5)
            if not raw or len(raw) >= _MAX_REQUEST_BYTES:
                raise BootstrapControlError("control request is empty or too large")
            request = json.loads(raw)
            if not isinstance(request, dict):
                raise BootstrapControlError("control request must be a JSON object")
            response = {"ok": True, "result": self._dispatch(request)}
        except (
            BootstrapControlError,
            BootstrapOperationRejected,
            json.JSONDecodeError,
            TypeError,
            ValueError,
        ) as exc:
            response = {
                "ok": False,
                "error": {
                    "code": type(exc).__name__,
                    "message": str(exc),
                },
            }
        except Exception:
            logger.exception("unexpected bootstrap control failure")
            response = {
                "ok": False,
                "error": {
                    "code": "internal_error",
                    "message": "bootstrap control operation failed",
                },
            }
        writer.write(
            json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
            + b"\n"
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    def _dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        operation = request.get("op")
        if operation == "health":
            return self._service.health()
        if operation == "descriptor":
            return self._service.public_descriptor()
        if operation == "host.prove":
            return self._service.prove_host(request.get("challenge"))
        if operation == "commissioning.endpoint":
            return self._service.commissioning_endpoint()
        if operation == "dev.issue":
            raw_ttl = request.get("ttl_seconds")
            ttl = None if raw_ttl is None else int(raw_ttl)
            return self._service.issue_development_descriptor(ttl)
        if operation == "dev.show":
            return self._service.development_descriptor_status()
        raise BootstrapControlError(f"unknown control operation: {operation!r}")


class BootstrapControlClient:
    def __init__(self, socket_path: Path) -> None:
        self._socket_path = socket_path

    async def request(self, operation: str, **parameters: Any) -> dict[str, Any]:
        reader, writer = await asyncio.open_unix_connection(self._socket_path)
        try:
            payload = {"op": operation, **parameters}
            writer.write(
                json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n"
            )
            await writer.drain()
            raw = await asyncio.wait_for(reader.readline(), timeout=5)
            try:
                response = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise BootstrapControlError(
                    "bootstrap control response is not valid JSON"
                ) from exc
            if not isinstance(response, dict) or response.get("ok") is not True:
                error = response.get("error", {}) if isinstance(response, dict) else {}
                raise BootstrapControlError(
                    str(error.get("message", "bootstrap control request failed"))
                )
            result = response.get("result")
            if not isinstance(result, dict):
                raise BootstrapControlError("bootstrap control response is malformed")
            return result
        finally:
            writer.close()
            await writer.wait_closed()
