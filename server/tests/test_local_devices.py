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

from eidolon_admin_server.app.control_plane.contracts import KernelBodyEndpointPage
from eidolon_admin_server.local_api.devices import (
    AdminOwnerDevicesClient,
    DeviceInventoryError,
    owner_device_inventory_view,
)

from eidolon_sdk.device_foundation.v1.testing import named_device_instance_id

from tests.body_mesh_support import endpoint_document, endpoint_page

# Tests name the device they mean; the name becomes a real device
# instance id, which is a digest of a key and never a chosen string.
_DEVICE_MOUNTED = named_device_instance_id("device-mounted")
_DEVICE_READY = named_device_instance_id("device-ready")
_DEVICE_REMOVED = named_device_instance_id("device-removed")


def _endpoint_page(*, owner_id: str = "owner-1") -> KernelBodyEndpointPage:
    now = datetime(2026, 8, 9, 10, 0, tzinfo=UTC).isoformat()
    return KernelBodyEndpointPage.model_validate(
        endpoint_page(
            endpoint_document(
                device_id=_DEVICE_READY,
                owner_id=owner_id,
                companion_id="companion-1",
                assignment_revision=3,
                mount_revision=3,
                updated_at=now,
            ),
            # A Body nobody has decided about: no assignment row at all, which
            # is a different state from one that was cleared.
            endpoint_document(
                device_id=_DEVICE_MOUNTED,
                owner_id=owner_id,
                assigned=False,
                updated_at=now,
            ),
            # What removal leaves behind: the Body outlives its mount so the
            # device can come back to the same Eidolon, and it says it is gone.
            endpoint_document(
                device_id=_DEVICE_REMOVED,
                owner_id=owner_id,
                companion_id=None,
                assignment_revision=5,
                mount_revision=5,
                present=False,
                updated_at=now,
            ),
        )
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
            for mount in _endpoint_page(owner_id=owner_id).endpoints
            if mount.present
        ),
        next_cursor=None,
        observed_at=now,
    )


@pytest.mark.asyncio
async def test_admin_device_client_uses_exact_owner_route_and_service_token() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.raw_path == (
            b"/api/control-plane/v1/owners/owner-domain%3Aone/body-endpoints"
        )
        assert request.headers["authorization"] == "Bearer local-service-token"
        return httpx.Response(
            200,
            json=_endpoint_page(owner_id="owner-domain:one").model_dump(mode="json"),
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    subject = AdminOwnerDevicesClient(
        base_url="http://127.0.0.1:9000",
        service_token="local-service-token",
        timeout_seconds=1,
        client=http_client,
    )
    try:
        result = await subject.list_body_endpoints("owner-domain:one")
    finally:
        await http_client.aclose()
    assert result.endpoints[0].owner_id == "owner-domain:one"


def test_mobile_device_projection_is_sanitized_and_explicitly_mount_scoped() -> None:
    view = owner_device_inventory_view(
        endpoints=_endpoint_page(),
        bound_owner_id="owner-1",
        claims=_claims(),
    )

    payload = view.model_dump(mode="json")
    assert payload["contract_version"] == "1"
    assert payload["coverage"] == "active-kernel-mounts-with-owner-scoped-hub-claims"
    assert payload["devices"][0]["claim"]["state"] == "active"
    assert "request_id" not in payload["devices"][0]["body"]
    assert "fingerprint" not in payload["devices"][0]["body"]


def test_the_revision_a_phone_echoes_back_is_the_bodys_not_the_devices() -> None:
    """Two facts, two compare-and-swap tokens.

    They shared one while the Companion was a field on the mount, which is how
    re-claiming a device silently discarded who answered through it. A Body
    nobody has decided about carries zero, and that is a value a change is
    expected to send rather than a missing one.
    """

    view = owner_device_inventory_view(
        endpoints=_endpoint_page(),
        bound_owner_id="owner-1",
        claims=_claims(),
    )
    bodies = {item.device_id: item.body for item in view.devices}

    assert bodies[_DEVICE_READY].assignment_revision == 3
    assert bodies[_DEVICE_READY].answering_companion_id == "companion-1"
    assert bodies[_DEVICE_MOUNTED].assignment_revision == 0
    assert bodies[_DEVICE_MOUNTED].answering_companion_id is None
    assert bodies[_DEVICE_MOUNTED].selection_provenance is None


def test_mobile_device_projection_drops_the_mounts_removal_left_behind() -> None:
    """A removed device leaves an inactive Kernel mount. It is not membership."""

    view = owner_device_inventory_view(
        endpoints=_endpoint_page(),
        bound_owner_id="owner-1",
        claims=_claims(),
    )

    assert [item.device_id for item in view.devices] == [
        _DEVICE_READY,
        _DEVICE_MOUNTED,
    ]


def test_mobile_device_projection_rejects_cross_owner_membership() -> None:
    with pytest.raises(DeviceInventoryError) as caught:
        owner_device_inventory_view(
            endpoints=_endpoint_page(owner_id="owner-other"),
            bound_owner_id="owner-1",
            claims=_claims("owner-other"),
        )
    assert caught.value.status_code == 409
