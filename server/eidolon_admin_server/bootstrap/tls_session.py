"""TLS-over-reliable-link session using Python's OpenSSL-backed SSLObject."""

from __future__ import annotations

import json
import ssl
from typing import Any

from .commissioning_protocol import CommissioningProtocolSession
from .ports import CommissioningLink, CommissioningLinkClosed


_MAX_APPLICATION_MESSAGE = 64 * 1024


class CommissioningTlsSession:
    """Turn an ordered BLE link into an authenticated encrypted JSON session."""

    def __init__(self, link: CommissioningLink, context: ssl.SSLContext) -> None:
        self._link = link
        self._incoming = ssl.MemoryBIO()
        self._outgoing = ssl.MemoryBIO()
        self._tls = context.wrap_bio(self._incoming, self._outgoing, server_side=True)
        self._plaintext = bytearray()

    @staticmethod
    def server_context(pem_path: str) -> ssl.SSLContext:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(pem_path)
        return context

    async def handshake(self) -> None:
        while True:
            try:
                self._tls.do_handshake()
                await self._flush_encrypted()
                return
            except ssl.SSLWantReadError:
                await self._flush_encrypted()
                await self._receive_encrypted()

    async def receive_json(self) -> dict[str, Any]:
        while True:
            newline = self._plaintext.find(b"\n")
            if newline >= 0:
                raw = bytes(self._plaintext[:newline])
                del self._plaintext[: newline + 1]
                if not raw or len(raw) > _MAX_APPLICATION_MESSAGE:
                    raise CommissioningLinkClosed(
                        "commissioning request is empty or too large"
                    )
                try:
                    value = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise CommissioningLinkClosed(
                        "commissioning request is not valid JSON"
                    ) from exc
                if not isinstance(value, dict):
                    raise CommissioningLinkClosed(
                        "commissioning request must be a JSON object"
                    )
                return value
            if len(self._plaintext) > _MAX_APPLICATION_MESSAGE:
                raise CommissioningLinkClosed("commissioning request is too large")
            try:
                chunk = self._tls.read(16 * 1024)
                if not chunk:
                    raise CommissioningLinkClosed("commissioning TLS session closed")
                self._plaintext.extend(chunk)
            except ssl.SSLWantReadError:
                await self._flush_encrypted()
                await self._receive_encrypted()
            except ssl.SSLZeroReturnError as exc:
                raise CommissioningLinkClosed(
                    "commissioning TLS session closed"
                ) from exc

    async def send_json(self, value: dict[str, Any]) -> None:
        payload = (
            json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            + b"\n"
        )
        if len(payload) > _MAX_APPLICATION_MESSAGE:
            raise CommissioningLinkClosed("commissioning response is too large")
        offset = 0
        while offset < len(payload):
            try:
                offset += self._tls.write(payload[offset:])
            except ssl.SSLWantWriteError:
                await self._flush_encrypted()
        await self._flush_encrypted()

    async def close(self) -> None:
        try:
            self._tls.unwrap()
        except (ssl.SSLError, ssl.SSLWantReadError):
            pass
        try:
            await self._flush_encrypted()
        except CommissioningLinkClosed:
            pass
        await self._link.close()

    async def _receive_encrypted(self) -> None:
        chunk = await self._link.receive()
        if not chunk:
            raise CommissioningLinkClosed("commissioning link returned empty data")
        self._incoming.write(chunk)

    async def _flush_encrypted(self) -> None:
        while self._outgoing.pending:
            await self._link.send(self._outgoing.read())


async def run_commissioning_tls_session(
    link: CommissioningLink,
    context: ssl.SSLContext,
    protocol: CommissioningProtocolSession,
) -> None:
    session = CommissioningTlsSession(link, context)
    try:
        await session.handshake()
        while True:
            request = await session.receive_json()
            response = await protocol.handle(request)
            await session.send_json(response)
    except CommissioningLinkClosed:
        return
    finally:
        await session.close()
