"""The Host-side recovery edge: an Owner whose phone lost its Controller key.

Every test here is a state a real Host reached on 2026-08-25. The Host had been
claimed, the App had been reinstalled, and the two things an Owner could try
both failed: one with a refusal that named nothing actionable, the other with
``internal_error`` and ``retryable: true`` over a conflict that would never
stop conflicting.
"""

from __future__ import annotations

import asyncio
import base64
import dataclasses
import hashlib
import sqlite3
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from eidolon_admin_server.bootstrap.adapters.network import (
    InMemoryNetworkProvisioning,
)
from eidolon_admin_server.bootstrap.adapters.persistence import (
    InMemoryBootstrapStateStore,
    SQLiteBootstrapStateStore,
)
from eidolon_admin_server.bootstrap.commissioning_protocol import (
    CommissioningProtocolSession,
)
from eidolon_admin_server.bootstrap.commissioning_service import CommissioningService
from eidolon_admin_server.bootstrap.config import BootstrapMode, BootstrapSettings
from eidolon_admin_server.bootstrap.domain import ClaimState, NetworkState
from eidolon_admin_server.bootstrap.identity import HostIdentityManager
from eidolon_admin_server.bootstrap.ports import BootstrapStateConflict
from eidolon_admin_server.bootstrap.service import (
    BootstrapOperationRejected,
    BootstrapService,
)


def _settings(tmp_path: Path) -> BootstrapSettings:
    return BootstrapSettings(
        # Development only so the Host identity can be generated in a tmp dir;
        # no fixed dev Setup code, so every code here is drawn the way a
        # shipped Host draws one.
        mode=BootstrapMode.DEVELOPMENT,
        state_dir=tmp_path / "state",
        runtime_dir=tmp_path / "run",
        control_socket=tmp_path / "run" / "control.sock",
        ble_service_uuid="179e2e95-b1ee-5aa5-8dcf-7519b6c7ac52",
    )


def _controller_public_key() -> str:
    public_der = (
        ec.generate_private_key(ec.SECP256R1())
        .public_key()
        .public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    )
    return base64.urlsafe_b64encode(public_der).rstrip(b"=").decode("ascii")


def _store(tmp_path: Path, store_kind: str):
    if store_kind == "memory":
        return InMemoryBootstrapStateStore()
    return SQLiteBootstrapStateStore(tmp_path / "bootstrap.sqlite3")


def _host(tmp_path: Path, store_kind: str):
    settings = _settings(tmp_path)
    store = _store(tmp_path, store_kind)
    network = InMemoryNetworkProvisioning(current_ssid="Home", access_points=[])
    service = BootstrapService(
        settings=settings,
        store=store,
        identity_manager=HostIdentityManager(settings.identity_key_path, settings.mode),
        network=network,
    )
    service.initialize()
    service.reconcile_network_state(NetworkState.CONNECTED)
    return service, store, network


def _claim(store, network, descriptor: dict, public_key: str, name: str) -> dict:
    commissioning = CommissioningService(store=store, network=network)
    authorization = commissioning.authorize(
        session_id=descriptor["commissioning_id"],
        secret=descriptor["setup_code"],
    )
    return commissioning.claim_controller(
        authorization,
        {
            "public_key": public_key,
            "display_name": name,
            "platform": "android",
        },
    )


@pytest.mark.parametrize("store_kind", ["memory", "sqlite"])
def test_the_phone_that_was_claimed_before_can_claim_again_after_recovery(
    tmp_path: Path, store_kind: str
) -> None:
    """The one phone an Owner actually holds is the one this used to refuse.

    Reinstalling the App throws away the Controller credential, not the key
    pair behind it, so the phone comes back with the same public key and the
    same derived controller_id. The Grant row from the previous epoch was
    still occupying that identity globally, so the insert collided:
    ``UNIQUE constraint failed: controller_grants.public_key_fingerprint``,
    raised past every conflict handler and delivered as internal_error.
    """

    service, store, network = _host(tmp_path, store_kind)
    try:
        phone = _controller_public_key()
        first = _claim(store, network, service.issue_setup_code(300), phone, "Pad")
        assert store.get_state().claim_state is ClaimState.CLAIMED

        recovery = service.open_controller_recovery_window(ttl_seconds=900)
        assert recovery["after"]["claim_state"] == "unclaimed"

        again = _claim(
            store, network, recovery["setup_session"], phone, "Pad (reinstalled)"
        )
        assert again["controller"]["controller_id"] == first["controller"]["controller_id"]
        assert again["controller"]["reset_epoch"] == store.get_state().reset_epoch
        assert again["controller"]["revoked_at"] is None
        assert store.get_state().claim_state is ClaimState.CLAIMED

        # And it really holds the Host now: the refusal that started this is gone.
        listed = service.list_controllers(
            controller_id=again["controller"]["controller_id"]
        )["controllers"]
        assert [grant["display_name"] for grant in listed] == ["Pad (reinstalled)"]
    finally:
        service.shutdown()


@pytest.mark.parametrize("store_kind", ["memory", "sqlite"])
def test_the_older_epoch_grant_survives_as_history(
    tmp_path: Path, store_kind: str
) -> None:
    """A recovery revokes authority; it does not rewrite what happened.

    The device side keeps a tombstone for every retired instance. The Host
    side needs the same thing, and it needs it to stop being load-bearing.
    """

    service, store, network = _host(tmp_path, store_kind)
    try:
        phone = _controller_public_key()
        _claim(store, network, service.issue_setup_code(300), phone, "Pad")
        recovery = service.open_controller_recovery_window(ttl_seconds=900)
        _claim(store, network, recovery["setup_session"], phone, "Pad")

        epochs = {
            (grant.controller_id, grant.reset_epoch): grant.revoked_at
            for grant in store.list_controllers()
        }
        assert len(epochs) == 2
        by_epoch = {epoch: revoked for (_, epoch), revoked in epochs.items()}
        assert by_epoch[0] is not None
        assert by_epoch[1] is None
    finally:
        service.shutdown()


@pytest.mark.parametrize("store_kind", ["memory", "sqlite"])
def test_claiming_twice_in_one_epoch_returns_the_grant_already_held(
    tmp_path: Path, store_kind: str
) -> None:
    """A phone that already holds this Host asking again is not a failure.

    This is the retry an Owner produces by pressing the button a second time
    after the first attempt looked stuck. Refusing it told the phone to start
    over, which produced exactly the same request.
    """

    service, store, network = _host(tmp_path, store_kind)
    try:
        phone = _controller_public_key()
        first = _claim(store, network, service.issue_setup_code(300), phone, "Pad")
        second = _claim(
            store,
            network,
            service.invite_controller(
                controller_id=first["controller"]["controller_id"], ttl_seconds=300
            ),
            phone,
            "Pad again",
        )
        assert second["controller"]["controller_id"] == first["controller"]["controller_id"]
        assert second["controller"]["display_name"] == "Pad"
        assert len(store.list_controllers()) == 1
    finally:
        service.shutdown()


@pytest.mark.parametrize("store_kind", ["memory", "sqlite"])
def test_a_store_conflict_is_a_conflict_and_never_an_unnamed_exception(
    tmp_path: Path, store_kind: str
) -> None:
    """Whatever a store refuses, it refuses in the one language callers catch.

    ``CommissioningService.claim_controller`` catches BootstrapStateConflict
    and nothing else, so any other exception type becomes internal_error with
    retryable: true. A store speaking sqlite3 to its caller is that bug.
    """

    service, store, network = _host(tmp_path, store_kind)
    try:
        phone = _controller_public_key()
        descriptor = service.issue_setup_code(300)
        held = _claim(store, network, descriptor, phone, "Pad")["controller"]
        grant = store.get_controller(held["controller_id"])
        assert grant is not None

        # A consumed session presented again, by a phone that is not the one
        # that consumed it.
        with pytest.raises(BootstrapStateConflict):
            store.claim_controller(
                session_id=descriptor["commissioning_id"],
                secret_hash="0" * 64,
                grant=dataclasses.replace(
                    grant,
                    controller_id="ectrl-" + "b" * 20,
                    public_key_fingerprint="sha256:" + "b" * 64,
                ),
                now="2026-08-25T00:00:00Z",
            )

        # A session that was never issued at all.
        with pytest.raises(BootstrapStateConflict):
            store.claim_controller(
                session_id="never-issued",
                secret_hash="0" * 64,
                grant=grant,
                now="2026-08-25T00:00:00Z",
            )

        # A different key claiming an identity this epoch already holds.
        second = service.invite_controller(
            controller_id=grant.controller_id, ttl_seconds=300
        )
        with pytest.raises(BootstrapStateConflict, match="already holds this identity"):
            store.claim_controller(
                session_id=second["commissioning_id"],
                secret_hash=hashlib.sha256(
                    second["setup_code"].encode("utf-8")
                ).hexdigest(),
                grant=dataclasses.replace(grant, public_key="different-key"),
                now="2026-08-25T00:00:00Z",
            )
    finally:
        service.shutdown()


def test_a_reclaim_never_reaches_a_phone_as_internal_error(tmp_path: Path) -> None:
    """The wire envelope is the part an Owner actually reads.

    internal_error means "the Host hit something it has no name for", and it
    is marked retryable. A phone told that about a deterministic conflict
    retries forever, which is what happened on the Pi three times in six
    minutes.
    """

    service, store, network = _host(tmp_path, "sqlite")
    try:
        phone = _controller_public_key()
        first = _claim(store, network, service.issue_setup_code(300), phone, "Pad")
        invitation = service.invite_controller(
            controller_id=first["controller"]["controller_id"], ttl_seconds=300
        )

        session = CommissioningProtocolSession(
            CommissioningService(store=store, network=network)
        )

        async def run() -> list[dict]:
            authenticated = await session.handle(
                {
                    "contract_version": "1",
                    "request_id": "00000000-0000-4000-8000-000000000001",
                    "operation": "session.authenticate",
                    "payload": {
                        "commissioning_id": invitation["commissioning_id"],
                        "setup_code": invitation["setup_code"],
                    },
                }
            )
            claimed = await session.handle(
                {
                    "contract_version": "1",
                    "request_id": "00000000-0000-4000-8000-000000000002",
                    "operation": "claim.complete",
                    "payload": {
                        "public_key": phone,
                        "display_name": "Pad",
                        "platform": "android",
                    },
                }
            )
            return [authenticated, claimed]

        authenticated, claimed = asyncio.run(run())
        assert authenticated["ok"] is True
        assert claimed["ok"] is True, claimed
        assert (
            claimed["result"]["controller"]["controller_id"]
            == first["controller"]["controller_id"]
        )
    finally:
        service.shutdown()


def test_the_field_sequence_end_to_end_over_the_wire(tmp_path: Path) -> None:
    """The whole of 2026-08-25, from the Pi's side, in one test.

    Claimed Host, App reinstalled, phone refused; operator opens the recovery
    window; the same phone walks the same Setup flow it walked out of the box
    and gets in. Every step is the wire envelope a phone actually receives, so
    a regression cannot hide behind a service call that looks fine.
    """

    service, store, network = _host(tmp_path, "sqlite")
    try:
        phone = _controller_public_key()
        _claim(store, network, service.issue_setup_code(300), phone, "Pad")

        recovery = service.open_controller_recovery_window(ttl_seconds=900)
        window = recovery["setup_session"]
        session = CommissioningProtocolSession(
            CommissioningService(store=store, network=network)
        )

        async def run() -> list[dict]:
            replies = []
            for index, (operation, payload) in enumerate(
                (
                    (
                        "session.authenticate",
                        {
                            "commissioning_id": window["commissioning_id"],
                            "setup_code": window["setup_code"],
                        },
                    ),
                    ("wifi.scan", {}),
                    (
                        "claim.complete",
                        {
                            "public_key": phone,
                            "display_name": "Pad",
                            "platform": "android",
                        },
                    ),
                ),
                start=1,
            ):
                replies.append(
                    await session.handle(
                        {
                            "contract_version": "1",
                            "request_id": f"00000000-0000-4000-8000-{index:012d}",
                            "operation": operation,
                            "payload": payload,
                        }
                    )
                )
            return replies

        authenticated, scanned, claimed = asyncio.run(run())
        assert authenticated["ok"] is True, authenticated
        # Keeping the current network is the button that used to answer
        # internal_error: the Host is already connected, so nothing else is
        # asked of it before the claim.
        assert scanned["result"]["current_network"]["state"] == "connected"
        assert claimed["ok"] is True, claimed
        assert claimed["result"]["state"]["claim_state"] == "claimed"
        assert claimed["result"]["state"]["reset_epoch"] == 1
    finally:
        service.shutdown()


@pytest.mark.parametrize("store_kind", ["memory", "sqlite"])
def test_recovery_revokes_and_opens_the_window_in_one_act(
    tmp_path: Path, store_kind: str
) -> None:
    """Revoking authority and opening the way back are one intention.

    Split across two operator commands, the Host had a reachable state with
    no Controller and no window: nobody could manage it and no phone could
    claim it, and getting out needed a second command nothing had asked for.
    """

    service, store, network = _host(tmp_path, store_kind)
    try:
        phone = _controller_public_key()
        claimed = _claim(store, network, service.issue_setup_code(300), phone, "Pad")

        recovery = service.open_controller_recovery_window(ttl_seconds=900)
        assert recovery["revoked_controllers"] == [
            claimed["controller"]["controller_id"]
        ]
        assert recovery["after"]["reset_epoch"] == 1
        assert recovery["after"]["claim_state"] == "unclaimed"
        assert "owner_binding" in recovery["preserved"]

        window = recovery["setup_session"]
        assert window["commissioning_id"]
        assert window["setup_code"]
        assert window["expires_at"] > window["issued_at"]

        # The window is the Host's own durable record of the act, readable by
        # a phone over BLE without any credential.
        endpoint = service.commissioning_endpoint()
        assert endpoint["setup_session"]["commissioning_id"] == (
            window["commissioning_id"]
        )
        assert endpoint["reset_epoch"] == 1
    finally:
        service.shutdown()


@pytest.mark.parametrize("store_kind", ["memory", "sqlite"])
def test_the_recovery_window_is_bounded(tmp_path: Path, store_kind: str) -> None:
    """An unbounded window is a Host anyone who passes by may take over."""

    service, _store, _network = _host(tmp_path, store_kind)
    try:
        with pytest.raises(BootstrapOperationRejected, match="ttl_seconds"):
            service.open_controller_recovery_window(ttl_seconds=30)
        with pytest.raises(BootstrapOperationRejected, match="ttl_seconds"):
            service.open_controller_recovery_window(ttl_seconds=90000)
        default = service.open_controller_recovery_window(ttl_seconds=None)
        assert default["setup_session"]["expires_at"] > (
            default["setup_session"]["issued_at"]
        )
    finally:
        service.shutdown()


def test_recovery_leaves_the_owner_and_the_workspace_alone(tmp_path: Path) -> None:
    """The promise the operator help text makes has to survive the reset."""

    service, store, network = _host(tmp_path, "sqlite")
    try:
        phone = _controller_public_key()
        claimed = _claim(store, network, service.issue_setup_code(300), phone, "Pad")
        service.bind_controller_owner(
            controller_id=claimed["controller"]["controller_id"],
            reset_epoch=0,
            owner_id="owner-1",
        )

        recovery = service.open_controller_recovery_window(ttl_seconds=900)
        reclaimed = _claim(store, network, recovery["setup_session"], phone, "Pad")

        principal = service.validate_controller(
            reclaimed["controller"]["controller_id"], 1
        )
        assert principal["owner_id"] == "owner-1"
        assert store.get_state().workspace_state.value == "ready"
    finally:
        service.shutdown()


def test_sqlite_v6_grants_migrate_to_epoch_scoped_identity(tmp_path: Path) -> None:
    """A Host in the field carries the table that caused this.

    The upgrade has to keep the rows — they are the only record of who held
    this Host — while removing their power to block the phone that comes back.
    """

    path = tmp_path / "bootstrap.sqlite3"
    store = SQLiteBootstrapStateStore(path)
    store.open()
    try:
        store.initialize("2026-08-05T00:00:00Z")
    finally:
        store.close()

    # Rebuild a v6 controller_grants: identity global rather than epoch-scoped.
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        DROP TABLE controller_grants;
        CREATE TABLE controller_grants (
            controller_id TEXT PRIMARY KEY,
            public_key TEXT NOT NULL,
            public_key_fingerprint TEXT NOT NULL UNIQUE,
            role TEXT NOT NULL,
            display_name TEXT NOT NULL,
            platform TEXT NOT NULL,
            reset_epoch INTEGER NOT NULL CHECK (reset_epoch >= 0),
            created_at TEXT NOT NULL,
            revoked_at TEXT
        );
        INSERT INTO controller_grants VALUES (
            'ectrl-0123456789abcdefabcd', 'AAAA', 'sha256:aaaa', 'host_admin',
            'Pad', 'android', 0, '2026-08-05T00:00:00Z', '2026-08-06T00:00:00Z'
        );
        UPDATE bootstrap_state SET reset_epoch = 1, claim_state = 'unclaimed';
        PRAGMA user_version = 6;
        """
    )
    connection.commit()
    connection.close()

    store = SQLiteBootstrapStateStore(path)
    store.open()
    try:
        store.initialize("2026-08-25T00:00:00Z")
        assert store.connection.execute("PRAGMA user_version").fetchone()[0] == 7

        history = store.list_controllers()
        assert [(grant.controller_id, grant.reset_epoch) for grant in history] == [
            ("ectrl-0123456789abcdefabcd", 0)
        ]
        # The row is history now, not an occupant: it belongs to epoch 0 and
        # this Host is on epoch 1.
        assert store.get_controller("ectrl-0123456789abcdefabcd") is None

        store.connection.execute(
            """
            INSERT INTO controller_grants (
                controller_id, public_key, public_key_fingerprint, role,
                display_name, platform, reset_epoch, created_at, revoked_at
            ) VALUES (
                'ectrl-0123456789abcdefabcd', 'AAAA', 'sha256:aaaa', 'host_admin',
                'Pad', 'android', 1, '2026-08-25T00:00:00Z', NULL
            )
            """
        )
        store.connection.commit()
        current = store.get_controller("ectrl-0123456789abcdefabcd")
        assert current is not None and current.reset_epoch == 1
    finally:
        store.close()
