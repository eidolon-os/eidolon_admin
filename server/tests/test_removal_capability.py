"""The Workflow receives a fixed removal capability, never Hub's admin key."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from eidolon_admin_server.app.control_plane.contracts import (
    DeviceRef,
    HubClaimRevocationResult,
    HubDevice,
)
from eidolon_admin_server.lifecycle_workflow.capability import (
    BrokerMarkerIssuer,
    RemovalCapabilityBroker,
    RemovalCapabilityReady,
    ResolveRemovalTarget,
    RevokeRemovalTarget,
)


pytestmark = pytest.mark.unit


def _ref() -> DeviceRef:
    return DeviceRef(
        device_instance_id="device-1",
        owner_domain_id="owner-1",
        claim_generation=3,
        trust_epoch=4,
        accepted_manifest_digest="sha256:" + "a" * 64,
    )


def _device() -> HubDevice:
    now = datetime.now(UTC)
    return HubDevice.model_validate(
        {
            "operation": "device.directory-entry",
            "device_id": "device-1",
            "owner_scope": "owner-1",
            "display_name": "Device",
            "device_kind": "generic",
            "manifest": {
                "schema_version": 1,
                "title": "Device",
                "properties": [],
                "actions": [],
                "events": [],
                "media": [],
            },
            "manifest_revision": "sha256:" + "a" * 64,
            "lifecycle_state": "approved",
            "enrolled_at": now,
            "updated_at": now,
            "device_ref": _ref(),
        }
    )


class _Credentials:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def issue(self, **_kwargs):
        raise AssertionError("generic hub-admin issuer must never be used")

    def issue_removal_discovery(self, **kwargs) -> str:
        self.calls.append(("discovery", kwargs))
        return "Bearer removal-discovery"

    def issue_removal_intent(self, **kwargs) -> str:
        self.calls.append(("revoke", kwargs))
        return "Bearer removal-intent"


class _Hub:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def get_device(self, **kwargs):
        self.calls.append(("get", kwargs))
        return _device()

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
    resolved = await broker._dispatch(
        ResolveRemovalTarget(
            owner_id="owner-1",
            controller_id="ectrl-0123456789abcdefabcd",
            device_id="device-1",
        )
    )
    revoked = await broker._dispatch(
        RevokeRemovalTarget(
            controller_id="ectrl-0123456789abcdefabcd",
            intent_id="removal-intent-" + "a" * 32,
            device_ref=_ref(),
            reason="owner-removed",
            command_id="revoke-claim-1",
        )
    )

    assert ready.ready is True
    assert resolved.device is not None
    assert resolved.device.device_ref == _ref()
    assert revoked.revocation is not None
    assert [name for name, _ in credentials.calls] == ["discovery", "revoke"]
    assert hub.calls[1][1]["device_ref"] == _ref()


def test_workflow_marker_is_not_a_bearer_credential() -> None:
    marker = BrokerMarkerIssuer().issue_removal_intent(
        controller_id="ectrl-0123456789abcdefabcd",
        intent_id="removal-intent-" + "a" * 32,
        device_ref=_ref(),
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
