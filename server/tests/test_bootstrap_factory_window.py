"""The window the code printed on the chassis is for (ADR-0007).

A device is unboxed, powered on, and claimed with the code on it. Nothing in
that reaches the Host's control socket, so the window cannot wait for
`commissioning-code` — which is why there was no out-of-box flow at all, and
why an Orange Pi 5 Max that a phone could see and connect to still refused it.

The switch is the file's presence. Three conditions guard the window, and each
one is the whole answer on its own.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
import base64

from eidolon_admin_server.bootstrap.adapters.network import InMemoryNetworkProvisioning
from eidolon_admin_server.bootstrap.adapters.persistence import (
    InMemoryBootstrapStateStore,
)
from eidolon_admin_server.bootstrap.commissioning_protocol import (
    CommissioningProtocolSession,
)
from eidolon_admin_server.bootstrap.commissioning_service import CommissioningService
from eidolon_admin_server.bootstrap.config import BootstrapMode, BootstrapSettings
from eidolon_admin_server.bootstrap.domain import NetworkState
from eidolon_admin_server.bootstrap.identity import HostIdentityManager
from eidolon_admin_server.bootstrap.service import BootstrapService

FACTORY_CODE = "48213097"


def _settings(tmp_path: Path) -> BootstrapSettings:
    return BootstrapSettings(
        mode=BootstrapMode.DEVELOPMENT,
        state_dir=tmp_path / "state",
        runtime_dir=tmp_path / "run",
        control_socket=tmp_path / "run" / "control.sock",
        ble_service_uuid="179e2e95-b1ee-5aa5-8dcf-7519b6c7ac52",
    )


def _write_factory_code(settings: BootstrapSettings, code: str) -> None:
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    path = settings.factory_setup_code_path
    path.write_text(code + "\n", encoding="utf-8")
    path.chmod(0o600)


def _service(
    settings: BootstrapSettings, store: InMemoryBootstrapStateStore | None = None
) -> tuple[BootstrapService, InMemoryBootstrapStateStore]:
    store = store or InMemoryBootstrapStateStore()
    service = BootstrapService(
        settings=settings,
        store=store,
        identity_manager=HostIdentityManager(settings.identity_key_path, settings.mode),
    )
    service.initialize()
    return service, store


def _controller_public_key() -> str:
    der = (
        ec.generate_private_key(ec.SECP256R1())
        .public_key()
        .public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    )
    return base64.urlsafe_b64encode(der).rstrip(b"=").decode("ascii")


# --- the switch ------------------------------------------------------------


def test_a_factory_code_opens_a_standing_window_on_first_start(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    _write_factory_code(settings, FACTORY_CODE)

    service, store = _service(settings)

    window = service.commissioning_endpoint()["setup_session"]
    assert window is not None
    # No clock on it: the device may have been in its box for a month.
    assert window["expires_at"] is None
    assert store.latest_commissioning_session().expires_at is None


def test_no_file_means_no_window_which_is_how_a_shared_code_stays_safe(
    tmp_path: Path,
) -> None:
    """The absence of the file is the switch.

    A development fleet sharing one code (99999990 today) must not stand a
    window up — an unexpiring window plus a code everyone knows is an open
    door. Not delivering the file is the whole mechanism; there is deliberately
    no mode check to get wrong.
    """

    settings = _settings(tmp_path)

    service, store = _service(settings)

    assert service.commissioning_endpoint()["setup_session"] is None
    assert store.latest_commissioning_session() is None


def test_a_guessable_factory_code_is_refused_and_said_out_loud(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """An unexpiring window is the last place to accept 11111111."""

    settings = _settings(tmp_path)
    _write_factory_code(settings, "11111111")

    with caplog.at_level(logging.ERROR, logger="eidolon.bootstrap"):
        service, store = _service(settings)

    assert service.commissioning_endpoint()["setup_session"] is None
    assert store.latest_commissioning_session() is None
    message = "\n".join(record.getMessage() for record in caplog.records)
    assert "usable" in message
    # Says it refused without printing what it read.
    assert "11111111" not in message


# --- the two conditions that are not the file ------------------------------


def test_a_claimed_host_opens_no_window_however_it_restarts(tmp_path: Path) -> None:
    """A claimed Host's window is its owner's to open.

    Standing one up here would hand a second Host Admin to anyone who read the
    chassis, on every reboot.
    """

    settings = _settings(tmp_path)
    _write_factory_code(settings, FACTORY_CODE)
    service, store = _service(settings)
    service.reconcile_network_state(NetworkState.CONNECTED)

    session = CommissioningProtocolSession(
        CommissioningService(store=store, network=InMemoryNetworkProvisioning())
    )
    import asyncio

    async def claim() -> dict:
        assert (
            await session.handle(
                {
                    "contract_version": "1",
                    "request_id": "11111111-1111-4111-8111-111111111111",
                    "operation": "session.authenticate",
                    "payload": {
                        "commissioning_id": service.commissioning_endpoint()[
                            "setup_session"
                        ]["commissioning_id"],
                        "setup_code": FACTORY_CODE,
                    },
                }
            )
        )["ok"] is True
        return await session.handle(
            {
                "contract_version": "1",
                "request_id": "22222222-2222-4222-8222-222222222222",
                "operation": "claim.complete",
                "payload": {
                    "public_key": _controller_public_key(),
                    "display_name": "Pad",
                    "platform": "android",
                },
            }
        )

    claimed = asyncio.run(claim())
    assert claimed["ok"] is True, claimed
    assert service.commissioning_endpoint()["setup_session"] is None

    # Restarting does not reopen it, and the file is still sitting there.
    restarted, _ = _service(settings, store)
    assert restarted.commissioning_endpoint()["setup_session"] is None


def test_a_restart_does_not_replace_a_window_an_operator_just_issued(
    tmp_path: Path,
) -> None:
    """Minting supersedes, so doing this unconditionally would discard one."""

    settings = _settings(tmp_path)
    _write_factory_code(settings, FACTORY_CODE)
    service, store = _service(settings)
    issued = service.issue_setup_code(600)

    restarted, _ = _service(settings, store)

    assert (
        restarted.commissioning_endpoint()["setup_session"]["commissioning_id"]
        == issued["commissioning_id"]
    )


def test_the_factory_code_is_what_the_window_accepts(tmp_path: Path) -> None:
    """The point of the whole thing: the code on the box claims the device."""

    settings = _settings(tmp_path)
    _write_factory_code(settings, FACTORY_CODE)
    service, store = _service(settings)
    commissioning = CommissioningService(
        store=store, network=InMemoryNetworkProvisioning()
    )
    window = service.commissioning_endpoint()["setup_session"]

    authorization = commissioning.authorize(
        session_id=window["commissioning_id"], secret=FACTORY_CODE
    )

    assert authorization is not None
