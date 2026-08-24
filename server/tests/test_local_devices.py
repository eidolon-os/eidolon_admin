from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
from eidolon_sdk.device_foundation.v1 import (
    BusinessOwnerId,
    ClaimPage,
    ClaimRecord,
    ClaimState,
    ManifestRef,
    OwnerDomainId,
)

from eidolon_admin_server.app.control_plane.contracts import KernelMountPage
from eidolon_admin_server.local_api.devices import (
    AdminOwnerDevicesClient,
    DeviceInventoryError,
    owner_device_inventory_view,
)


def _mount_page(*, owner_id: str = "owner-1") -> KernelMountPage:
    now = datetime(2026, 8, 9, 10, 0, tzinfo=UTC)
    return KernelMountPage.model_validate(
        {
            "operation": "kernel.device-mount-page",
            "next_cursor": None,
            "mounts": [
                {
                    "operation": "kernel.device-mount",
                    "device_id": "device-ready",
                    "owner_id": owner_id,
                    "device_ref": {
                        "device_instance_id": "device-ready",
                        "owner_domain_id": owner_id,
                        "owner_domain_generation": 1,
                        "claim_generation": 1,
                        "trust_epoch": 1,
                    },
                    "attached_companion_id": "companion-1",
                    "revision": 3,
                    "created_at": now.isoformat(),
                    "updated_at": now.isoformat(),
                    "request_id": "internal-request-not-for-mobile",
                    "fingerprint": "sha256:" + "0" * 64,
                    "active": True,
                },
                {
                    "operation": "kernel.device-mount",
                    "device_id": "device-mounted",
                    "owner_id": owner_id,
                    "device_ref": {
                        "device_instance_id": "device-mounted",
                        "owner_domain_id": owner_id,
                        "owner_domain_generation": 1,
                        "claim_generation": 1,
                        "trust_epoch": 1,
                    },
                    "attached_companion_id": None,
                    "revision": 1,
                    "created_at": now.isoformat(),
                    "updated_at": now.isoformat(),
                    "request_id": "another-internal-request",
                    "fingerprint": "sha256:" + "1" * 64,
                    "active": True,
                },
                {
                    "operation": "kernel.device-mount",
                    "device_id": "device-removed",
                    "owner_id": owner_id,
                    "device_ref": {
                        "device_instance_id": "device-removed",
                        "owner_domain_id": owner_id,
                        "owner_domain_generation": 1,
                        "claim_generation": 1,
                        "trust_epoch": 1,
                    },
                    "attached_companion_id": None,
                    "revision": 5,
                    "created_at": now.isoformat(),
                    "updated_at": now.isoformat(),
                    "request_id": "removal-internal-request",
                    "fingerprint": "sha256:" + "2" * 64,
                    "active": False,
                },
            ],
        }
    )


def _claims(owner_id: str = "owner-1") -> ClaimPage:
    domain = OwnerDomainId(owner_id)
    now = datetime(2026, 8, 9, 10, 0, tzinfo=UTC)
    return ClaimPage(
        owner_domain_id=domain,
        items=tuple(
            ClaimRecord(
                device_ref=mount.device_ref,
                business_owner_id=BusinessOwnerId("owner_account_1"),
                manifest_ref=ManifestRef(
                    manifest_id=f"manifest-{mount.device_id}",
                    revision=1,
                    digest="sha256:" + "a" * 64,
                ),
                state=ClaimState.ACTIVE,
                revision=1,
                updated_at=now,
            )
            for mount in _mount_page(owner_id=owner_id).mounts
            if mount.active
        ),
        next_cursor=None,
        observed_at=now,
    )


@pytest.mark.asyncio
async def test_admin_device_client_uses_exact_owner_route_and_service_token() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.raw_path == (
            b"/api/control-plane/v1/owners/owner-domain%3Aone/device-mounts"
        )
        assert request.headers["authorization"] == "Bearer local-service-token"
        return httpx.Response(
            200,
            json=_mount_page(owner_id="owner-domain:one").model_dump(mode="json"),
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    subject = AdminOwnerDevicesClient(
        base_url="http://127.0.0.1:9000",
        service_token="local-service-token",
        timeout_seconds=1,
        client=http_client,
    )
    try:
        result = await subject.list_mounts("owner-domain:one")
    finally:
        await http_client.aclose()
    assert result.mounts[0].owner_id == "owner-domain:one"


def test_mobile_device_projection_is_sanitized_and_explicitly_mount_scoped() -> None:
    view = owner_device_inventory_view(
        mounts=_mount_page(),
        bound_owner_id="owner-1",
        claims=_claims(),
    )

    payload = view.model_dump(mode="json")
    assert payload["contract_version"] == "1"
    assert payload["coverage"] == "active-kernel-mounts-with-owner-scoped-hub-claims"
    assert payload["devices"][0]["claim"]["state"] == "active"
    assert "request_id" not in payload["devices"][0]["mount"]
    assert "fingerprint" not in payload["devices"][0]["mount"]


def test_mobile_device_projection_drops_the_mounts_removal_left_behind() -> None:
    """A removed device leaves an inactive Kernel mount. It is not membership."""

    view = owner_device_inventory_view(
        mounts=_mount_page(),
        bound_owner_id="owner-1",
        claims=_claims(),
    )

    assert [item.device_id for item in view.devices] == [
        "device-ready",
        "device-mounted",
    ]


def test_mobile_device_projection_rejects_cross_owner_membership() -> None:
    with pytest.raises(DeviceInventoryError) as caught:
        owner_device_inventory_view(
            mounts=_mount_page(owner_id="owner-other"),
            bound_owner_id="owner-1",
            claims=_claims("owner-other"),
        )
    assert caught.value.status_code == 409
