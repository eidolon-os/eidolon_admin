from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from eidolon_admin_server.bootstrap.adapters.network import (
    InMemoryNetworkProvisioning,
)
from eidolon_admin_server.bootstrap.adapters.persistence import (
    BOOTSTRAP_SCHEMA_VERSION,
    InMemoryBootstrapStateStore,
    SQLiteBootstrapStateStore,
)
from eidolon_admin_server.bootstrap.config import BootstrapMode, BootstrapSettings
from eidolon_admin_server.bootstrap.commissioning_service import (
    CommissioningRequestRejected,
    CommissioningService,
)
from eidolon_admin_server.bootstrap.commissioning_protocol import (
    CommissioningProtocolSession,
)
from eidolon_admin_server.bootstrap.domain import ClaimState, NetworkState
from eidolon_admin_server.bootstrap.identity import HostIdentityManager
from eidolon_admin_server.bootstrap.ports import (
    BootstrapStateStore,
    NetworkChangeRequest,
    NetworkProvisioning,
    NetworkProvisioningError,
    WifiAccessPoint,
)
from eidolon_admin_server.bootstrap.service import (
    BootstrapOperationRejected,
    BootstrapService,
)


def _settings(tmp_path: Path) -> BootstrapSettings:
    return BootstrapSettings(
        mode=BootstrapMode.DEVELOPMENT,
        state_dir=tmp_path / "state",
        runtime_dir=tmp_path / "run",
        control_socket=tmp_path / "run" / "control.sock",
        ble_service_uuid="179e2e95-b1ee-5aa5-8dcf-7519b6c7ac52",
    )


def test_service_runs_against_state_store_port_without_sqlite(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = InMemoryBootstrapStateStore()
    assert isinstance(store, BootstrapStateStore)

    service = BootstrapService(
        settings=settings,
        store=store,
        identity_manager=HostIdentityManager(
            settings.identity_key_path,
            settings.mode,
        ),
    )
    service.initialize()
    try:
        credential = service.issue_setup_code(300)
        assert credential["host_id"].startswith("ehost-")
        assert store.latest_commissioning_session() is not None
        assert service.health()["state"]["claim_state"] == "unclaimed"
    finally:
        service.shutdown()


@pytest.mark.asyncio
async def test_in_memory_network_adapter_stages_confirms_and_rolls_back() -> None:
    network = InMemoryNetworkProvisioning(current_ssid="old-network")
    assert isinstance(network, NetworkProvisioning)

    request = NetworkChangeRequest(
        operation_id="network-op-1",
        ssid="new-network",
        passphrase="not-persisted-by-this-adapter",
    )
    assert "not-persisted" not in repr(request)

    staged = await network.begin_change(request)
    assert staged.state is NetworkState.STAGING
    assert staged.current_ssid == "old-network"
    assert staged.staged_ssid == "new-network"

    rolled_back = await network.rollback("network-op-1")
    assert rolled_back.state is NetworkState.CONNECTED
    assert rolled_back.current_ssid == "old-network"

    await network.begin_change(
        NetworkChangeRequest(operation_id="network-op-2", ssid="new-network")
    )
    confirmed = await network.confirm("network-op-2")
    assert confirmed.state is NetworkState.CONNECTED
    assert confirmed.current_ssid == "new-network"
    assert confirmed.active_operation_id is None

    cleared = await network.forget_all_wifi_profiles()
    assert cleared.state is NetworkState.UNCONFIGURED
    assert cleared.current_ssid is None


@pytest.mark.asyncio
async def test_network_adapter_rejects_overlapping_or_wrong_operation() -> None:
    network = InMemoryNetworkProvisioning()
    await network.begin_change(
        NetworkChangeRequest(operation_id="network-op-1", ssid="network")
    )

    with pytest.raises(NetworkProvisioningError, match="another"):
        await network.begin_change(
            NetworkChangeRequest(operation_id="network-op-2", ssid="other")
        )
    with pytest.raises(NetworkProvisioningError, match="does not match"):
        await network.confirm("network-op-2")


def test_sqlite_v6_keeps_authority_and_drops_what_no_longer_holds_state(
    tmp_path: Path,
) -> None:
    path = tmp_path / "bootstrap.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE bootstrap_state (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
            reset_epoch INTEGER NOT NULL CHECK (reset_epoch >= 0),
            claim_state TEXT NOT NULL,
            network_state TEXT NOT NULL,
            workspace_state TEXT NOT NULL,
            recovery_state TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE daemon_runs (
            run_id TEXT PRIMARY KEY,
            pid INTEGER NOT NULL,
            started_at TEXT NOT NULL,
            stopped_at TEXT
        );
        CREATE TABLE commissioning_sessions (
            session_id TEXT PRIMARY KEY,
            secret_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            consumed_at TEXT,
            revoked_at TEXT
        );
        INSERT INTO bootstrap_state VALUES (
            1, 7, 'claimed', 'connected', 'ready', 'normal',
            '2026-08-05T00:00:00Z'
        );
        INSERT INTO daemon_runs VALUES (
            'old-run', 42, '2026-08-05T00:00:00Z', NULL
        );
        INSERT INTO commissioning_sessions VALUES (
            'session-1', 'hash-only', '2026-08-05T00:00:00Z',
            '2026-08-05T00:05:00Z', NULL, NULL
        );
        PRAGMA user_version = 1;
        """
    )
    connection.close()

    store = SQLiteBootstrapStateStore(path)
    store.open()
    try:
        store.initialize("2026-08-05T01:00:00Z")
        tables = {
            row[0]
            for row in store.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        version = store.connection.execute("PRAGMA user_version").fetchone()[0]

        assert version == BOOTSTRAP_SCHEMA_VERSION
        assert "daemon_runs" not in tables
        # recovery_state only ever held "normal"; a Host that carried one is
        # migrated out of it without losing the authority beside it.
        columns = {
            row[1]
            for row in store.connection.execute("PRAGMA table_info(bootstrap_state)")
        }
        assert "recovery_state" not in columns
        # And the two that recorded what the Data plane held. The authority
        # beside them — the epoch, the claim, the sessions — is untouched.
        assert "workspace_state" not in columns
        assert "owner_id" not in columns
        assert store.get_state().reset_epoch == 7
        assert store.latest_commissioning_session().session_id == "session-1"
        assert store.latest_commissioning_session().failed_attempts == 0
    finally:
        store.close()


def test_fresh_sqlite_contains_only_durable_authority_tables(tmp_path: Path) -> None:
    store = SQLiteBootstrapStateStore(tmp_path / "bootstrap.sqlite3")
    store.open()
    try:
        store.initialize("2026-08-05T00:00:00Z")
        tables = {
            row[0]
            for row in store.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert tables == {
            "bootstrap_state",
            "commissioning_sessions",
            "controller_grants",
            "bootstrap_operations",
        }
    finally:
        store.close()


def test_sqlite_v4_host_state_migrates_without_ever_gaining_an_owner_column(
    tmp_path: Path,
) -> None:
    path = tmp_path / "bootstrap.sqlite3"
    store = SQLiteBootstrapStateStore(path)
    store.open()
    try:
        store.initialize("2026-08-05T00:00:00Z")
        store.connection.execute(
            """
            INSERT INTO controller_grants (
                controller_id, public_key, public_key_fingerprint, role,
                display_name, platform, reset_epoch, created_at, revoked_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "ectrl-v4-controller",
                _controller_public_key(),
                "v4-fingerprint",
                "host_admin",
                "Existing controller",
                "android",
                0,
                "2026-08-05T00:00:00Z",
                None,
            ),
        )
        store.connection.commit()
    finally:
        store.close()

    # Rebuild what a real v4 Host carried: a workspace_state column, no owner
    # binding yet (v5 added that), and a recovery_state that never held
    # anything but "normal".
    connection = sqlite3.connect(path)
    connection.execute(
        "ALTER TABLE bootstrap_state ADD COLUMN workspace_state TEXT NOT NULL "
        "DEFAULT 'ready'"
    )
    connection.execute(
        "ALTER TABLE bootstrap_state ADD COLUMN recovery_state TEXT NOT NULL DEFAULT 'normal'"
    )
    connection.execute("PRAGMA user_version = 4")
    connection.commit()
    connection.close()

    migrated = SQLiteBootstrapStateStore(path)
    migrated.open()
    try:
        migrated.initialize("2026-08-05T01:00:00Z")
        controller = migrated.get_controller("ectrl-v4-controller")
        assert controller is not None
        # The ladder runs v5 (add owner_id), v6, v7, then v8 (drop both). A
        # Host that came all the way from v4 must arrive with neither column,
        # not with the one v5 gave it -- the whole point of v8 is that no
        # version of this store keeps a claim about the Data plane.
        columns = {
            row[1]
            for row in migrated.connection.execute(
                "PRAGMA table_info(bootstrap_state)"
            )
        }
        assert "owner_id" not in columns
        assert "workspace_state" not in columns
        assert migrated.get_state().claim_state is ClaimState.UNCLAIMED
        assert (
            migrated.connection.execute("PRAGMA user_version").fetchone()[0]
            == BOOTSTRAP_SCHEMA_VERSION
        )
    finally:
        migrated.close()


def _controller_public_key() -> str:
    public_der = (
        ec.generate_private_key(ec.SECP256R1())
        .public_key()
        .public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    )
    return base64.urlsafe_b64encode(public_der).rstrip(b"=").decode("ascii")


def _protocol_request(operation: str, payload: dict, suffix: int) -> dict:
    return {
        "contract_version": "1",
        "request_id": f"00000000-0000-4000-8000-{suffix:012d}",
        "operation": operation,
        "payload": payload,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", ["memory", "sqlite"])
async def test_commissioning_service_completes_network_then_atomic_claim(
    tmp_path: Path,
    store_kind: str,
) -> None:
    settings = _settings(tmp_path)
    store = (
        InMemoryBootstrapStateStore()
        if store_kind == "memory"
        else SQLiteBootstrapStateStore(tmp_path / "bootstrap.sqlite3")
    )
    network = InMemoryNetworkProvisioning(
        current_ssid="Existing",
        access_points=[
            WifiAccessPoint("Home", 58, True),
            WifiAccessPoint("Cafe", 34, False),
            WifiAccessPoint("Home", 81, True),
        ],
    )
    bootstrap = BootstrapService(
        settings=settings,
        store=store,
        identity_manager=HostIdentityManager(
            settings.identity_key_path,
            settings.mode,
        ),
        network=network,
    )
    bootstrap.initialize()
    descriptor = bootstrap.issue_setup_code(300)
    bootstrap.reconcile_network_state(NetworkState.CONNECTED)
    commissioning = CommissioningService(store=store, network=network)
    try:
        authorization = commissioning.authorize(
            session_id=descriptor["commissioning_id"],
            secret=descriptor["setup_code"],
        )
        scanned = await commissioning.scan_networks(authorization)
        assert scanned["current_network"] == {
            "state": "connected",
            "ssid": "Existing",
        }
        assert scanned["networks"] == [
            {"ssid": "Home", "signal": 81, "secured": True},
            {"ssid": "Cafe", "signal": 34, "secured": False},
        ]

        operation_id = "32c421a3-e0df-40f9-8f75-68745ae39d81"
        staged = await commissioning.configure_network(
            authorization,
            {
                "operation_id": operation_id,
                "ssid": "Home",
                "passphrase": "correct horse battery staple",
            },
        )
        assert staged["operation"]["state"] == "waiting_confirmation"

        controller_payload = {
            "public_key": _controller_public_key(),
            "display_name": "Manson 的手机",
            "platform": "android",
        }

        with pytest.raises(CommissioningRequestRejected, match="network must"):
            # Store enforces the same ordering even if a caller skips confirm.
            commissioning.claim_controller(authorization, controller_payload)

        confirmed = await commissioning.confirm_network(authorization, operation_id)
        assert confirmed["operation"]["state"] == "succeeded"
        claimed = commissioning.claim_controller(authorization, controller_payload)
        assert claimed["state"]["claim_state"] == "claimed"
        assert claimed["controller"]["role"] == "host_admin"
        assert len(store.list_controllers()) == 1

        # A claim tells this store who holds the Host and nothing about what
        # the Data plane holds. There is no binding step here any more: the
        # Owner a phone acts for is resolved by the session that needs it,
        # from the only component that can answer.
        assert not hasattr(bootstrap, "bind_controller_owner")
        assert set(claimed["state"]) == {
            "reset_epoch",
            "claim_state",
            "network_state",
            "updated_at",
        }

        retried = commissioning.claim_controller(authorization, controller_payload)
        assert retried["controller"] == claimed["controller"]

        with pytest.raises(
            CommissioningRequestRejected, match="Commissioning session is unavailable"
        ):
            commissioning.status(authorization)

        if store_kind == "sqlite":
            dump = "\n".join(store.connection.iterdump())
            assert "correct horse battery staple" not in dump

        reset = await bootstrap.reset_development_state(
            forget_wifi_profiles=True,
        )
        assert reset["before"]["claim_state"] == "claimed"
        assert reset["after"]["claim_state"] == "unclaimed"
        assert reset["after"]["network_state"] == "unconfigured"
        assert reset["after"]["reset_epoch"] == 1
        assert reset["forgot_wifi_profiles"] is True
        # Claim reset revokes Controllers and reaches nothing of the Owner's.
        # It used to have to preserve a workspace row here; it now cannot
        # affect one, because it holds none to preserve or to get wrong.
        assert set(reset["after"]) == {
            "reset_epoch",
            "claim_state",
            "network_state",
            "updated_at",
        }
        assert store.list_controllers()[0].revoked_at is not None
        with pytest.raises(CommissioningRequestRejected, match="unavailable"):
            commissioning.status(authorization)
    finally:
        bootstrap.shutdown()


@pytest.mark.parametrize("store_kind", ["memory", "sqlite"])
def test_commissioning_locks_setup_code_after_five_wrong_attempts(
    tmp_path: Path,
    store_kind: str,
) -> None:
    settings = _settings(tmp_path)
    store = (
        InMemoryBootstrapStateStore()
        if store_kind == "memory"
        else SQLiteBootstrapStateStore(tmp_path / "bootstrap.sqlite3")
    )
    bootstrap = BootstrapService(
        settings=settings,
        store=store,
        identity_manager=HostIdentityManager(settings.identity_key_path, settings.mode),
    )
    bootstrap.initialize()
    try:
        descriptor = bootstrap.issue_setup_code(300)
        commissioning = CommissioningService(
            store=store,
            network=InMemoryNetworkProvisioning(),
        )
        wrong_code = "00000012" if descriptor["setup_code"] != "00000012" else "00000013"
        for attempt in range(5):
            with pytest.raises(
                CommissioningRequestRejected,
                match="Commissioning session is unavailable",
            ):
                commissioning.authorize(
                    session_id=descriptor["commissioning_id"],
                    secret=wrong_code,
                )
            expected = 0 if attempt == 4 else attempt + 1
            assert store.latest_commissioning_session().failed_attempts == expected

        # Locked, not revoked. Revoking an unexpiring window means nobody can
        # reopen it, and the window this rule guards is the one whose code is
        # printed on the chassis — five wrong guesses from across the room used
        # to brick a device out of its box (ADR-0007).
        session = store.latest_commissioning_session()
        assert session.revoked_at is None
        assert session.locked_until is not None

        # The right code is refused too, while the lock stands.
        with pytest.raises(CommissioningRequestRejected):
            commissioning.authorize(
                session_id=descriptor["commissioning_id"],
                secret=descriptor["setup_code"],
            )
        assert store.get_state().claim_state.value == "unclaimed"
        assert store.list_controllers() == []

        # And it is a wait, not an ending: the same window opens again once the
        # lock has passed, with a full allowance rather than one guess left.
        # `locked_until` is the moment it reopens, not the last moment it is
        # shut — "locked until T" reads that way, and the boundary has to be
        # one of the two.
        assert not session.is_open(session.created_at)
        assert session.is_open(session.locked_until)
        assert session.failed_attempts == 0
    finally:
        bootstrap.shutdown()


@pytest.mark.asyncio
async def test_claimed_controller_authenticates_and_changes_network(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    store = InMemoryBootstrapStateStore()
    bootstrap = BootstrapService(
        settings=settings,
        store=store,
        identity_manager=HostIdentityManager(settings.identity_key_path, settings.mode),
    )
    bootstrap.initialize()
    network = InMemoryNetworkProvisioning(
        access_points=[WifiAccessPoint("New Home", 90, True)]
    )
    commissioning = CommissioningService(store=store, network=network)
    descriptor = bootstrap.issue_setup_code(300)
    initial = commissioning.authorize(
        session_id=descriptor["commissioning_id"],
        secret=descriptor["setup_code"],
    )
    first_operation = "c74b0000-5edc-4af7-af70-aefc7531d862"
    await commissioning.configure_network(
        initial,
        {"operation_id": first_operation, "ssid": "First Home"},
    )
    await commissioning.confirm_network(initial, first_operation)
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_der = private_key.public_key().public_bytes(
        Encoding.DER, PublicFormat.SubjectPublicKeyInfo
    )
    encoded_public = base64.urlsafe_b64encode(public_der).rstrip(b"=").decode()
    digest = hashlib.sha256(public_der).hexdigest()
    controller_id = f"ectrl-{digest[:20]}"
    commissioning.claim_controller(
        initial,
        {
            "controller_id": controller_id,
            "public_key": encoded_public,
            "display_name": "Primary phone",
            "platform": "android",
        },
    )

    protocol = CommissioningProtocolSession(commissioning)
    challenge_response = await protocol.handle(
        _protocol_request("controller.challenge", {"controller_id": controller_id}, 10)
    )
    assert challenge_response["ok"] is True
    challenge = challenge_response["result"]
    canonical = json.dumps(
        challenge,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    signature = private_key.sign(canonical, ec.ECDSA(hashes.SHA256()))
    authenticated = await protocol.handle(
        _protocol_request(
            "controller.authenticate",
            {
                **challenge,
                "signature": base64.urlsafe_b64encode(signature).rstrip(b"=").decode(),
            },
            11,
        )
    )
    assert authenticated["ok"] is True

    scanned = await protocol.handle(_protocol_request("wifi.scan", {}, 12))
    assert scanned["result"]["networks"][0]["ssid"] == "New Home"
    changed = await protocol.handle(
        _protocol_request(
            "wifi.configure",
            {
                "operation_id": "86c70054-f13e-4e21-aa75-e63157154302",
                "ssid": "New Home",
                "passphrase": "new-network-secret",
            },
            13,
        )
    )
    assert changed["result"]["operation"]["operation_type"] == "change_network"
    bootstrap.shutdown()


@pytest.mark.asyncio
async def test_daemon_restart_fails_interrupted_operation_and_unblocks_retry(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    store = InMemoryBootstrapStateStore()
    bootstrap = BootstrapService(
        settings=settings,
        store=store,
        identity_manager=HostIdentityManager(settings.identity_key_path, settings.mode),
    )
    bootstrap.initialize()
    descriptor = bootstrap.issue_setup_code(300)
    commissioning = CommissioningService(
        store=store,
        network=InMemoryNetworkProvisioning(),
    )
    authorization = commissioning.authorize(
        session_id=descriptor["commissioning_id"],
        secret=descriptor["setup_code"],
    )
    interrupted_id = "11d8113d-1792-4c31-bfbb-da413232e942"
    await commissioning.configure_network(
        authorization,
        {"operation_id": interrupted_id, "ssid": "First attempt"},
    )
    bootstrap.shutdown()

    restarted = BootstrapService(
        settings=settings,
        store=store,
        identity_manager=HostIdentityManager(settings.identity_key_path, settings.mode),
    )
    restarted.initialize()
    try:
        interrupted = store.get_operation(interrupted_id)
        assert interrupted is not None
        assert interrupted.state.value == "failed"
        assert interrupted.error_code == "daemon_restarted"
        assert store.get_state().network_state is NetworkState.DEGRADED

        restarted.reconcile_network_state(NetworkState.UNCONFIGURED)
        assert store.get_state().network_state is NetworkState.UNCONFIGURED

        authorization = commissioning.authorize(
            session_id=descriptor["commissioning_id"],
            secret=descriptor["setup_code"],
        )
        replacement_network = InMemoryNetworkProvisioning()
        replacement = CommissioningService(store=store, network=replacement_network)
        retried = await replacement.configure_network(
            authorization,
            {
                "operation_id": "179af39c-fccb-476d-9bf6-d7367eed0427",
                "ssid": "Second attempt",
            },
        )
        assert retried["operation"]["state"] == "waiting_confirmation"
    finally:
        restarted.shutdown()


def test_every_modelled_state_is_one_something_can_produce() -> None:
    """A value nothing writes is not a state a reader has to handle.

    recovery_state had four values and one writer, so a phone rendered a row
    promising the Host could report physical arming or a pending factory
    reset — neither of which any code path could ever set. The field is gone;
    what remains has to keep earning its place.

    ``WorkspaceState`` went the same way for a stronger reason. Two of its
    four modelled values never had a writer, and the two that did were writing
    a claim about the *Data* plane into Bootstrap's store — where it outlived
    the Workspace it described and stranded a Host. A state only another
    component can know is not one this store may hold at all.
    """

    from eidolon_admin_server.bootstrap.adapters.persistence import (
        memory as memory_store,
        sqlite as sqlite_store,
    )
    from eidolon_admin_server.bootstrap.domain import ClaimState, NetworkState

    sources = (
        Path(memory_store.__file__).read_text(encoding="utf-8"),
        Path(sqlite_store.__file__).read_text(encoding="utf-8"),
    )
    for enum in (ClaimState, NetworkState):
        for member in enum:
            written = any(f"{enum.__name__}.{member.name}" in source for source in sources)
            assert written, (
                f"{enum.__name__}.{member.name} has no writer in either store; "
                "delete it or write it"
            )


def _claim_a_phone(bootstrap, store, network, name: str) -> str:
    """Run one phone all the way to a Controller grant, and return its id."""

    descriptor = bootstrap.issue_setup_code(300)
    commissioning = CommissioningService(store=store, network=network)
    authorization = commissioning.authorize(
        session_id=descriptor["commissioning_id"],
        secret=descriptor["setup_code"],
    )
    claimed = commissioning.claim_controller(
        authorization,
        {
            "public_key": _controller_public_key(),
            "display_name": name,
            "platform": "android",
        },
    )
    return claimed["controller"]["controller_id"]


@pytest.mark.parametrize("store_kind", ["memory", "sqlite"])
def test_a_claimed_host_still_admits_a_second_phone(tmp_path: Path, store_kind: str) -> None:
    """Claim state alone used to refuse it, so a household with two people
    could only ever have one phone, or revoke the first to add the second."""

    settings = _settings(tmp_path)
    store = (
        InMemoryBootstrapStateStore()
        if store_kind == "memory"
        else SQLiteBootstrapStateStore(tmp_path / "bootstrap.sqlite3")
    )
    network = InMemoryNetworkProvisioning(current_ssid="Existing", access_points=[])
    bootstrap = BootstrapService(
        settings=settings,
        store=store,
        identity_manager=HostIdentityManager(settings.identity_key_path, settings.mode),
        network=network,
    )
    bootstrap.initialize()
    bootstrap.reconcile_network_state(NetworkState.CONNECTED)
    try:
        first = _claim_a_phone(bootstrap, store, network, "Pad")
        assert store.get_state().claim_state is ClaimState.CLAIMED

        # An invitation is asked for by a phone that already holds the Host.
        invitation = bootstrap.invite_controller(controller_id=first, ttl_seconds=300)
        commissioning = CommissioningService(store=store, network=network)
        authorization = commissioning.authorize(
            session_id=invitation["commissioning_id"],
            secret=invitation["setup_code"],
        )
        second = commissioning.claim_controller(
            authorization,
            {
                "public_key": _controller_public_key(),
                "display_name": "Phone",
                "platform": "ios",
            },
        )["controller"]["controller_id"]

        listed = bootstrap.list_controllers(controller_id=first)["controllers"]
        assert {grant["controller_id"] for grant in listed} == {first, second}
    finally:
        bootstrap.shutdown()


@pytest.mark.parametrize("store_kind", ["memory", "sqlite"])
def test_the_last_phone_cannot_revoke_itself_into_an_unmanageable_host(
    tmp_path: Path, store_kind: str
) -> None:
    """Removing the only Controller leaves a Host nobody can manage and no way
    back but the operator's own reset. That has to be asked for by name."""

    settings = _settings(tmp_path)
    store = (
        InMemoryBootstrapStateStore()
        if store_kind == "memory"
        else SQLiteBootstrapStateStore(tmp_path / "bootstrap.sqlite3")
    )
    network = InMemoryNetworkProvisioning(current_ssid="Existing", access_points=[])
    bootstrap = BootstrapService(
        settings=settings,
        store=store,
        identity_manager=HostIdentityManager(settings.identity_key_path, settings.mode),
        network=network,
    )
    bootstrap.initialize()
    bootstrap.reconcile_network_state(NetworkState.CONNECTED)
    try:
        first = _claim_a_phone(bootstrap, store, network, "Pad")

        with pytest.raises(BootstrapOperationRejected, match="last Controller"):
            bootstrap.revoke_controller(controller_id=first, target_id=first)

        invitation = bootstrap.invite_controller(controller_id=first)
        commissioning = CommissioningService(store=store, network=network)
        second = commissioning.claim_controller(
            commissioning.authorize(
                session_id=invitation["commissioning_id"],
                secret=invitation["setup_code"],
            ),
            {
                "public_key": _controller_public_key(),
                "display_name": "Phone",
                "platform": "ios",
            },
        )["controller"]["controller_id"]

        # With a peer present, a phone may hand back its own authority.
        bootstrap.revoke_controller(controller_id=first, target_id=first)
        remaining = bootstrap.list_controllers(controller_id=second)["controllers"]
        assert [grant["controller_id"] for grant in remaining] == [second]

        # And the survivor is now the last one again.
        with pytest.raises(BootstrapOperationRejected, match="last Controller"):
            bootstrap.revoke_controller(controller_id=second, target_id=second)
    finally:
        bootstrap.shutdown()
