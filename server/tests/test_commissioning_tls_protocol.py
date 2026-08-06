from __future__ import annotations

import asyncio
import base64
import json
import ssl
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from eidolon_admin_server.bootstrap.adapters.commissioning import (
    in_memory_commissioning_link_pair,
)
from eidolon_admin_server.bootstrap.adapters.network import InMemoryNetworkProvisioning
from eidolon_admin_server.bootstrap.adapters.persistence import (
    InMemoryBootstrapStateStore,
)
from eidolon_admin_server.bootstrap.commissioning_protocol import (
    CommissioningProtocolSession,
)
from eidolon_admin_server.bootstrap.commissioning_service import CommissioningService
from eidolon_admin_server.bootstrap.config import BootstrapMode, BootstrapSettings
from eidolon_admin_server.bootstrap.identity import HostIdentityManager
from eidolon_admin_server.bootstrap.ports import CommissioningLink, WifiAccessPoint
from eidolon_admin_server.bootstrap.service import BootstrapService
from eidolon_admin_server.bootstrap.tls_session import (
    CommissioningTlsSession,
    run_commissioning_tls_session,
)


def _settings(tmp_path: Path) -> BootstrapSettings:
    return BootstrapSettings(
        mode=BootstrapMode.DEVELOPMENT,
        state_dir=tmp_path / "state",
        runtime_dir=tmp_path / "run",
        control_socket=tmp_path / "run" / "control.sock",
        ble_service_uuid="179e2e95-b1ee-5aa5-8dcf-7519b6c7ac52",
    )


class _TlsJsonClient:
    def __init__(self, link: CommissioningLink, context: ssl.SSLContext, host: str):
        self._link = link
        self._incoming = ssl.MemoryBIO()
        self._outgoing = ssl.MemoryBIO()
        self._tls = context.wrap_bio(
            self._incoming,
            self._outgoing,
            server_side=False,
            server_hostname=host,
        )
        self._plaintext = bytearray()

    async def handshake(self) -> None:
        while True:
            try:
                self._tls.do_handshake()
                await self._flush()
                return
            except ssl.SSLWantReadError:
                await self._flush()
                self._incoming.write(await self._link.receive())

    async def request(self, payload: dict) -> dict:
        data = json.dumps(payload, separators=(",", ":")).encode() + b"\n"
        offset = 0
        while offset < len(data):
            try:
                offset += self._tls.write(data[offset:])
            except ssl.SSLWantWriteError:
                await self._flush()
        await self._flush()
        while True:
            newline = self._plaintext.find(b"\n")
            if newline >= 0:
                raw = bytes(self._plaintext[:newline])
                del self._plaintext[: newline + 1]
                return json.loads(raw)
            try:
                self._plaintext.extend(self._tls.read(16 * 1024))
            except ssl.SSLWantReadError:
                await self._flush()
                self._incoming.write(await self._link.receive())

    async def _flush(self) -> None:
        while self._outgoing.pending:
            await self._link.send(self._outgoing.read())


def _request(operation: str, payload: dict, suffix: int) -> dict:
    return {
        "contract_version": "1",
        "request_id": f"00000000-0000-4000-8000-{suffix:012d}",
        "operation": operation,
        "payload": payload,
    }


def test_commissioning_endpoint_binds_tls_key_to_host_signature(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = InMemoryBootstrapStateStore()
    service = BootstrapService(
        settings=settings,
        store=store,
        identity_manager=HostIdentityManager(settings.identity_key_path, settings.mode),
    )
    service.initialize()
    try:
        credential = service.issue_development_setup_code(300)
        endpoint = service.commissioning_endpoint()
        signature = base64.urlsafe_b64decode(endpoint.pop("signature") + "==")
        public_key = base64.urlsafe_b64decode(
            service.public_descriptor()["host_public_key"] + "="
        )
        canonical = json.dumps(
            endpoint,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, canonical)
        assert endpoint["purpose"] == "eidolon-ble-commissioning-endpoint-v1"
        assert (
            endpoint["host_public_key"]
            == service.public_descriptor()["host_public_key"]
        )
        assert (
            endpoint["development_setup"]["commissioning_id"]
            == credential["commissioning_id"]
        )
        assert endpoint["tls_spki_fingerprint"].startswith("sha256:")
        encoded = json.dumps(
            {
                **endpoint,
                "signature": base64.urlsafe_b64encode(signature).rstrip(b"=").decode(),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        assert len(encoded) <= 512
        assert settings.commissioning_tls_pem_path.stat().st_mode & 0o777 == 0o600
    finally:
        service.shutdown()


@pytest.mark.asyncio
async def test_pinned_tls_carries_authenticated_setup_protocol(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = InMemoryBootstrapStateStore()
    bootstrap = BootstrapService(
        settings=settings,
        store=store,
        identity_manager=HostIdentityManager(settings.identity_key_path, settings.mode),
    )
    bootstrap.initialize()
    descriptor = bootstrap.issue_development_setup_code(300)
    network = InMemoryNetworkProvisioning(
        access_points=[WifiAccessPoint("Home", 77, True)]
    )
    commissioning = CommissioningService(store=store, network=network)
    server_link, client_link = in_memory_commissioning_link_pair("tls-test")
    server_context = CommissioningTlsSession.server_context(
        str(settings.commissioning_tls_pem_path)
    )
    client_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    client_context.minimum_version = ssl.TLSVersion.TLSv1_2
    client_context.check_hostname = False
    client_context.verify_mode = ssl.CERT_REQUIRED
    client_context.load_verify_locations(
        cafile=str(settings.commissioning_tls_pem_path)
    )
    client = _TlsJsonClient(
        client_link,
        client_context,
        bootstrap.public_descriptor()["host_id"],
    )
    server_task = asyncio.create_task(
        run_commissioning_tls_session(
            server_link,
            server_context,
            CommissioningProtocolSession(commissioning),
        )
    )
    try:
        await client.handshake()
        authenticated = await client.request(
            _request(
                "session.authenticate",
                {
                    "commissioning_id": descriptor["commissioning_id"],
                    "setup_code": descriptor["setup_code"],
                },
                1,
            )
        )
        assert authenticated["ok"] is True
        scanned = await client.request(_request("wifi.scan", {}, 2))
        assert scanned["result"]["current_network"] == {
            "state": "unconfigured",
            "ssid": None,
        }
        assert scanned["result"]["networks"] == [
            {"ssid": "Home", "signal": 77, "secured": True}
        ]
        denied = await client.request(
            _request(
                "session.authenticate",
                {
                    "commissioning_id": descriptor["commissioning_id"],
                    "setup_code": "000000",
                },
                3,
            )
        )
        assert denied["error"]["code"] == "operation_conflict"
        assert denied["error"]["retryable"] is False
    finally:
        await client_link.close()
        await asyncio.wait_for(server_task, timeout=2)
        bootstrap.shutdown()
