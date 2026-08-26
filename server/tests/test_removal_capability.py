"""The Workflow receives a fixed removal capability, never Hub's admin key."""

from __future__ import annotations

import jwt
from pydantic import ValidationError
from eidolon_admin_server.app.control_plane.hub_credentials import HubAdminCredentialIssuer
from eidolon_sdk.device_foundation.v1 import BusinessOwnerId

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from eidolon_admin_server.app.control_plane.contracts import (
    DeviceRef,
    HubClaimRevocationResult,
)
from eidolon_admin_server.lifecycle_workflow.capability import (
    BrokerMarkerIssuer,
    RemovalCapabilityBroker,
    RemovalCapabilityReady,
    RevokeRemovalTarget,
)
from eidolon_admin_server.app.settings import GatewayConfig, get_settings


pytestmark = pytest.mark.unit


def _ref() -> DeviceRef:
    return DeviceRef(
        device_instance_id="device-1",
        owner_domain_id="owner-1",
        owner_domain_generation=1,
        claim_generation=3,
        trust_epoch=4,
    )


class _Credentials:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def issue(self, **_kwargs):
        raise AssertionError("generic hub-admin issuer must never be used")

    def issue_removal_intent(self, **kwargs) -> str:
        self.calls.append(("revoke", kwargs))
        return "Bearer removal-intent"


class _Hub:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def get_claim(self, **_kwargs):
        raise AssertionError("the broker no longer brokers a Claim read")

    async def revoke(self, **kwargs):
        self.calls.append(("revoke", kwargs))
        return HubClaimRevocationResult(
            operation="device.claim-revocation-result",
            command_id=kwargs["command_id"],
            outcome="committed",
            device_ref=kwargs["device_ref"],
            aggregate_revision=5,
            occurred_at=datetime.now(UTC),
            lifecycle_state="revoked",
        )


@pytest.mark.asyncio
async def test_broker_dispatch_has_no_generic_hub_admin_operation() -> None:
    credentials = _Credentials()
    hub = _Hub()
    broker = RemovalCapabilityBroker(
        socket_path=Path("/unused/broker.sock"),
        allowed_workflow_uid=41002,
        service=SimpleNamespace(hub=hub, hub_credentials=credentials),
    )

    ready = await broker._dispatch(RemovalCapabilityReady())
    revoked = await broker._dispatch(
        RevokeRemovalTarget(
            controller_id="ectrl-0123456789abcdefabcd",
            intent_id="removal-intent-" + "a" * 32,
            device_ref=_ref(),
            business_owner_id="owner_683f0000000000000000",
            reason="owner-removed",
            command_id="revoke-claim-1",
        )
    )

    assert ready.ready is True
    assert revoked.revocation is not None
    # Two operations, both generation-bound. A Claim read is no longer one of
    # them: the workflow used to re-derive its target and could refuse an
    # Owner's removal on that read alone.
    assert [name for name, _ in credentials.calls] == ["revoke"]
    assert hub.calls[0][1]["device_ref"] == _ref()


def test_workflow_marker_is_not_a_bearer_credential() -> None:
    marker = BrokerMarkerIssuer().issue_removal_intent(
        controller_id="ectrl-0123456789abcdefabcd",
        intent_id="removal-intent-" + "a" * 32,
        device_ref=_ref(),
        business_owner_id="owner_683f0000000000000000",
    )

    assert marker.startswith("broker:")
    assert not marker.startswith("Bearer ")


def test_workflow_unit_has_no_hub_or_local_static_secret() -> None:
    root = Path(__file__).resolve().parents[2]
    unit = (root / "deploy/systemd/eidolon-lifecycle-workflow.service").read_text()
    settings = (
        root / "server/eidolon_admin_server/lifecycle_workflow/settings.py"
    ).read_text()

    assert "EnvironmentFile=/etc/eidolon/lifecycle.env" not in unit
    assert "HUB_MANAGEMENT_JWT_SECRET" not in unit + settings
    assert "LOCAL_API_SERVICE_TOKEN" not in unit + settings


@pytest.mark.asyncio
async def test_admin_notifies_ready_only_after_capability_broker_is_bound(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from eidolon_admin_server.app import main as main_module

    events: list[str] = []

    class _Broker:
        def __init__(self, **_kwargs) -> None:
            pass

        async def start(self) -> None:
            events.append("broker-bound")

        async def close(self) -> None:
            events.append("broker-closed")

    class _Notifier:
        @classmethod
        def from_environ(cls):
            return cls()

        def ready(self, _status: str) -> None:
            events.append("admin-ready")

        def stopping(self, _status: str) -> None:
            events.append("admin-stopping")

    monkeypatch.setattr(main_module, "RemovalCapabilityBroker", _Broker)
    monkeypatch.setattr(main_module, "SystemdNotifier", _Notifier)
    monkeypatch.setattr(
        main_module.pwd,
        "getpwnam",
        lambda _name: SimpleNamespace(pw_uid=41002),
    )
    settings = get_settings().model_copy(
        update={"removal_capability_socket": tmp_path / "broker.sock"}
    )
    app = main_module.create_app(config=GatewayConfig(), settings=settings)

    async with app.router.lifespan_context(app):
        assert events == ["broker-bound", "admin-ready"]

    assert events == [
        "broker-bound",
        "admin-ready",
        "admin-stopping",
        "broker-closed",
    ]


def test_a_revocation_credential_speaks_the_surface_it_is_presented_to() -> None:
    """Revocation is an Admission route, so the credential must be Admission's.

    It used to mint the Management surface's vocabulary — ``actor_ref`` as a
    bare string, ``owner_id`` instead of ``owner_domain_id``, no
    ``business_owner_id`` — which is correct for reading and renaming devices
    and wrong for this route. Hub read ``claims["actor"]``, raised KeyError
    inside its ``except (JWTError, KeyError, TypeError, ValueError)``, and
    answered 401. Every removal anyone ever attempted was refused by this, and
    the refusal said nothing about credentials.

    The required claim names are asserted literally because they are one half
    of a contract whose other half lives in Hub. Hub pins the same set from its
    side; if either moves alone, one of the two goes red.
    """

    issuer = HubAdminCredentialIssuer(secret=b"s" * 32)
    ref = _ref()

    credential = issuer.issue_removal_intent(
        controller_id="ectrl-0123456789abcdefabcd",
        intent_id="removal-intent-" + "a" * 32,
        device_ref=ref,
        business_owner_id=BusinessOwnerId("owner_683f0000000000000000"),
    )

    scheme, _, token = credential.partition(" ")
    assert scheme == "Bearer"
    claims = jwt.decode(token, b"s" * 32, algorithms=["HS256"], audience="eidolon-admission")

    # Exactly what Hub's Admission authorizer reads.
    assert claims["presenter"] == claims["sub"]
    assert claims["owner_domain_id"] == str(ref.owner_domain_id)
    assert claims["business_owner_id"] == "owner_683f0000000000000000"
    assert claims["actor"]["owner_domain_id"] == str(ref.owner_domain_id)
    assert claims["actor"]["principal_type"] == "controller"
    assert set(claims["scopes"]) == set(claims["actor"]["granted_scopes"]) == {
        "device.claim.revoke"
    }
    # The Management vocabulary must not come back.
    assert "actor_ref" not in claims
    assert "owner_id" not in claims


def test_the_two_owner_identities_are_not_derived_from_each_other() -> None:
    """``owner_...`` and ``owner-...`` are different facts.

    Who the Owner is, and which Owner Domain this Host serves. Deriving one
    from the other produced a credential that could not be minted at all —
    which at least failed loudly; the dangerous version is one that mints and
    names the wrong Owner.
    """

    issuer = HubAdminCredentialIssuer(secret=b"s" * 32)

    with pytest.raises(ValidationError):
        issuer.issue_removal_intent(
            controller_id="ectrl-0123456789abcdefabcd",
            intent_id="removal-intent-" + "a" * 32,
            device_ref=_ref(),
            business_owner_id=BusinessOwnerId(str(_ref().owner_domain_id)),
        )
