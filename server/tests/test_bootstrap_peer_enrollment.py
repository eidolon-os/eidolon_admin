import pytest
from eidolon_admin_server.bootstrap.adapters.network import InMemoryNetworkProvisioning
from eidolon_admin_server.bootstrap.adapters.persistence import (
    InMemoryBootstrapStateStore,
    SQLiteBootstrapStateStore,
)
from eidolon_admin_server.bootstrap.commissioning_service import (
    CommissioningService,
    CommissioningRequestRejected,
)
from eidolon_admin_server.bootstrap.domain import NetworkState
from eidolon_admin_server.bootstrap.identity import HostIdentityManager
from eidolon_admin_server.bootstrap.service import BootstrapService
from .test_bootstrap_ports import _settings, _controller_public_key


@pytest.mark.parametrize("store_kind", ["memory", "sqlite"])
@pytest.mark.asyncio
async def test_offline_peer_enrollment_keeps_network_admin_only(tmp_path, store_kind):
    settings = _settings(tmp_path)
    store = (
        InMemoryBootstrapStateStore()
        if store_kind == "memory"
        else SQLiteBootstrapStateStore(tmp_path / "bootstrap.sqlite3")
    )
    network = InMemoryNetworkProvisioning(current_ssid=None, access_points=[])
    bootstrap = BootstrapService(
        settings=settings,
        store=store,
        network=network,
        identity_manager=HostIdentityManager(settings.identity_key_path, settings.mode),
    )
    bootstrap.initialize()
    service = CommissioningService(store=store, network=network)
    try:
        peers = []
        enrollments = []
        for name in ["First", "Second"]:
            invitation = bootstrap.issue_setup_code()
            setup = service.authorize(
                session_id=invitation["commissioning_id"],
                secret=invitation["setup_code"],
            )
            payload = {
                "public_key": _controller_public_key(),
                "display_name": name,
                "platform": "android",
            }
            enrollments.append((setup, payload))
            grant = service.claim_controller(setup, payload)["controller"]
            peers.append(service.authorize_controller(grant["controller_id"]))
        assert store.get_state().network_state is NetworkState.UNCONFIGURED
        assert len(store.list_controllers()) == 2
        invitation = bootstrap.issue_setup_code()
        setup = service.authorize(
            session_id=invitation["commissioning_id"], secret=invitation["setup_code"]
        )
        for method in [
            service.configure_network,
            service.confirm_network,
            service.rollback_network,
        ]:
            with pytest.raises(CommissioningRequestRejected) as denied:
                await method(
                    setup,
                    {}
                    if method == service.configure_network
                    else "32c421a3-e0df-40f9-8f75-68745ae39d81",
                )
            assert denied.value.code == "controller_denied"
        for i, peer in enumerate(peers):
            op = f"32c421a3-e0df-40f9-8f75-68745ae39d8{i}"
            result = await service.configure_network(
                peer,
                {
                    "operation_id": op,
                    "ssid": f"Network {i}",
                    "passphrase": "test-password",
                },
            )
            assert result["operation"]["state"] == "waiting_confirmation"
            if i == 0:
                with pytest.raises(CommissioningRequestRejected) as busy:
                    await service.configure_network(
                        peers[1],
                        {
                            "operation_id": "42c421a3-e0df-40f9-8f75-68745ae39d81",
                            "ssid": "Concurrent network",
                        },
                    )
                assert busy.value.code == "operation_conflict"
                assert store.get_operation(op).state.value == "waiting_confirmation"
            assert (await service.confirm_network(peer, op))["operation"][
                "state"
            ] == "succeeded"
        bootstrap.revoke_controller(
            controller_id=peers[1].grant.controller_id,
            target_id=peers[0].grant.controller_id,
        )
        with pytest.raises(CommissioningRequestRejected):
            await service.configure_network(peers[0], {})
        service.authorize_controller(peers[1].grant.controller_id)
        with pytest.raises(CommissioningRequestRejected):
            service.claim_controller(*enrollments[0])
        invitation = bootstrap.invite_controller(
            controller_id=peers[1].grant.controller_id
        )
        fresh = service.authorize(
            session_id=invitation["commissioning_id"], secret=invitation["setup_code"]
        )
        restored = service.claim_controller(fresh, enrollments[0][1])["controller"]
        assert restored["controller_id"] == peers[0].grant.controller_id
        assert (
            len(
                bootstrap.list_controllers(controller_id=restored["controller_id"])[
                    "controllers"
                ]
            )
            == 2
        )
        with pytest.raises(CommissioningRequestRejected):
            service.claim_controller(*enrollments[0])
    finally:
        bootstrap.shutdown()


def test_operation_history_upgrade_preserves_host_state(tmp_path):
    store = SQLiteBootstrapStateStore(tmp_path / "bootstrap.sqlite3")
    store.open()
    store.initialize("2026-01-01")
    before = store.get_state()
    store.connection.execute(
        "INSERT INTO bootstrap_operations VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "32c421a3-e0df-40f9-8f75-68745ae39d81",
            "initial_network",
            "succeeded",
            "Home",
            0,
            "2026-01-01",
            "2026-01-01",
            None,
        ),
    )
    store.connection.execute("PRAGMA user_version = 9")
    store.connection.commit()
    store.initialize("2026-10-09")
    assert store.get_state() == before
    operation = store.get_operation("32c421a3-e0df-40f9-8f75-68745ae39d81")
    assert operation.operation_type.value == "change_network"
    assert operation.state.value == "succeeded"
    store.close()
