"""The Owner-facing projection of what the Hub recorded happening."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from eidolon_admin_server.app.control_plane.contracts import OwnerDeviceHistory
from eidolon_admin_server.local_api.activity import owner_activity_view
from eidolon_admin_server.local_api.devices import (
    AdminOwnerDevicesClient,
    DeviceInventoryError,
)


def _device(
    device_id: str,
    *,
    owner_scope: str = "owner-1",
    display_name: str = "客厅的 Box-3",
    device_kind: str = "esp-box-3",
) -> dict:
    now = datetime(2026, 8, 17, 10, 0, tzinfo=UTC)
    return {
        "operation": "device.directory-entry",
        "device_id": device_id,
        "owner_scope": owner_scope,
        "display_name": display_name,
        "device_kind": device_kind,
        "manifest": {
            "schema_version": 1,
            "title": "Box-3",
            "properties": [],
            "actions": [],
            "events": [],
            "media": [],
        },
        "manifest_revision": "rev-1",
        "lifecycle_state": "approved",
        "enrolled_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }


def _event(
    position: int,
    *,
    event_type: str,
    principal_id: str,
    device_id: str = "24:ec:4a:52:f3:54",
    minute: int = 0,
    data: dict | None = None,
) -> dict:
    return {
        "operation": "device.management-event",
        "stream_position": position,
        "event_id": f"evt-{position}",
        "event_type": event_type,
        "source": "eidolon-hub/device-management",
        "principal_id": principal_id,
        "device_id": device_id,
        "occurred_at": datetime(2026, 8, 17, 10, minute, tzinfo=UTC).isoformat(),
        "data": data or {},
    }


def _history(**overrides) -> OwnerDeviceHistory:
    payload = {
        "operation": "admin.owner-device-history",
        "owner_id": "owner-1",
        "events": [
            _event(
                7,
                event_type="eidolon.device.revoked.v1",
                principal_id="eidolon-local-api/ectrl-49b622641c4b15d2933b",
                minute=20,
                data={"reason": "owner-removed"},
            ),
            _event(
                5,
                event_type="eidolon.device.approved.v1",
                principal_id="eidolon-local-api/ectrl-49b622641c4b15d2933b",
                minute=14,
                data={"owner_id": "owner-1"},
            ),
            _event(
                3,
                event_type="eidolon.device.enrolled.v1",
                principal_id="untrusted-device:24:ec:4a:52:f3:54",
                minute=6,
                data={"manifest_revision": "sha256:" + "a" * 64},
            ),
        ],
        "devices": [_device("24:ec:4a:52:f3:54")],
    }
    payload.update(overrides)
    return OwnerDeviceHistory.model_validate(payload)


def test_moments_say_what_happened_and_who_did_it() -> None:
    view = owner_activity_view(_history())

    payload = view.model_dump(mode="json")
    assert payload["contract_version"] == "1"
    # The screen is told what this covers, so a short list is not mistaken for
    # a quiet Host: nothing else on this Host is recorded here.
    assert payload["coverage"] == "device-lifecycle"
    assert [(item["kind"], item["actor"]) for item in payload["moments"]] == [
        ("device-removed", "owner"),
        ("device-accepted", "owner"),
        # A device enrolling speaks for itself; nobody has vouched for it yet.
        ("device-knocked", "device"),
    ]
    assert payload["moments"][0]["reason"] == "owner-removed"
    assert payload["moments"][1]["reason"] == ""


def test_a_moment_names_the_device_rather_than_identifying_it() -> None:
    view = owner_activity_view(_history())

    named = view.moments[0]
    assert named.device_name == "客厅的 Box-3"
    assert named.device_kind == "esp-box-3"
    # The identifier travels for the technical corner, never as the name.
    assert named.device_id == "24:ec:4a:52:f3:54"


def test_a_device_the_directory_cannot_name_is_not_named_by_its_identifier() -> None:
    view = owner_activity_view(_history(devices=[]))

    assert all(moment.device_name == "" for moment in view.moments)
    assert all(moment.device_id for moment in view.moments)


def test_an_unfamiliar_record_is_still_something_that_happened() -> None:
    """A Hub newer than this Host still gets to say a device changed."""

    view = owner_activity_view(
        _history(
            events=[
                _event(
                    9,
                    event_type="eidolon.device.renamed.v99",
                    principal_id="eidolon-hub/device-management",
                    minute=30,
                )
            ]
        )
    )

    assert len(view.moments) == 1
    assert view.moments[0].kind == "other"
    assert view.moments[0].actor == "host"
    assert view.moments[0].event_type == "eidolon.device.renamed.v99"


@pytest.mark.asyncio
async def test_history_client_asks_for_this_owner_and_this_controller() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.raw_path == (
            b"/api/control-plane/v1/owners/owner-1/device-history/ectrl-1?limit=25"
        )
        assert request.headers["authorization"] == "Bearer local-service-token"
        return httpx.Response(200, json=_history().model_dump(mode="json"))

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    subject = AdminOwnerDevicesClient(
        base_url="http://127.0.0.1:9000",
        service_token="local-service-token",
        timeout_seconds=1,
        client=http_client,
    )
    try:
        history = await subject.list_history("owner-1", "ectrl-1", 25)
    finally:
        await http_client.aclose()
    assert len(history.events) == 3


@pytest.mark.asyncio
async def test_a_history_that_could_not_be_read_is_not_an_empty_history() -> None:
    """The failure this whole slice exists to stop being invisible."""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "hub is down"})

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    subject = AdminOwnerDevicesClient(
        base_url="http://127.0.0.1:9000",
        service_token="local-service-token",
        timeout_seconds=1,
        client=http_client,
    )
    try:
        with pytest.raises(DeviceInventoryError) as caught:
            await subject.list_history("owner-1", "ectrl-1", 25)
    finally:
        await http_client.aclose()
    assert caught.value.status_code == 503


@pytest.mark.asyncio
async def test_a_history_belonging_to_someone_else_is_refused() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_history(owner_id="owner-somebody-else").model_dump(mode="json"),
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    subject = AdminOwnerDevicesClient(
        base_url="http://127.0.0.1:9000",
        service_token="local-service-token",
        timeout_seconds=1,
        client=http_client,
    )
    try:
        with pytest.raises(DeviceInventoryError) as caught:
            await subject.list_history("owner-1", "ectrl-1", 25)
    finally:
        await http_client.aclose()
    assert caught.value.status_code == 409
