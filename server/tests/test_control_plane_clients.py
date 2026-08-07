"""Transport failure semantics for bounded-context HTTP adapters."""

from __future__ import annotations

from pathlib import Path
from datetime import UTC, datetime

import httpx
import pytest

from eidolon_admin_server.app.control_plane.clients import (
    DATA_CONTRACT,
    HUB_CONTRACT,
    KERNEL_CONTRACT,
    DataAuthorityClient,
    HubManagementClient,
    KernelMountClient,
)
from eidolon_admin_server.app.control_plane.contracts import ServiceEndpoint
from eidolon_admin_server.app.control_plane.directory import SystemDirectoryClient
from eidolon_admin_server.app.control_plane.errors import AuthorityFailure

pytestmark = [pytest.mark.asyncio, pytest.mark.component]


class StaticDirectory:
    def __init__(self, addresses: dict[str, tuple[str, str]]) -> None:
        self.addresses = addresses

    async def resolve(
        self, *, service_id: str, endpoint_id: str, required_contract: str
    ):
        address, contract = self.addresses[service_id]
        assert contract == required_contract
        return ServiceEndpoint(
            operation="system.service-endpoint",
            service_id=service_id,
            endpoint_id=endpoint_id,
            protocol="http",
            address=address,
            contract=contract,
        )


def client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False)


def directory() -> StaticDirectory:
    return StaticDirectory(
        {
            "data": ("http://data.test", DATA_CONTRACT),
            "hub": ("http://hub.test", HUB_CONTRACT),
            "kernel": ("http://kernel.test", KERNEL_CONTRACT),
        }
    )


async def test_data_client_uses_exact_read_only_route_and_credential() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.raw_path == (
            b"/api/companion-authority/v1/companions/companion%20one"
        )
        assert request.headers["authorization"] == "Bearer admin-token"
        return httpx.Response(
            200,
            json={
                "operation": "companion.identity",
                "companion_id": "companion one",
                "owner_id": "owner-1",
                "lifecycle_state": "active",
            },
        )

    http_client = client(handler)
    try:
        subject = DataAuthorityClient(
            directory=directory(),  # type: ignore[arg-type]
            client=http_client,
            service_token="admin-token",
            timeout_seconds=1,
        )
        result = await subject.get_companion("companion one")
    finally:
        await http_client.aclose()
    assert result.lifecycle_state == "active"


async def test_data_client_requires_a_distinct_configured_credential() -> None:
    http_client = client(lambda _request: httpx.Response(500))
    try:
        subject = DataAuthorityClient(
            directory=directory(),  # type: ignore[arg-type]
            client=http_client,
            service_token="",
            timeout_seconds=1,
        )
        with pytest.raises(AuthorityFailure) as caught:
            await subject.get_companion("companion-1")
    finally:
        await http_client.aclose()
    assert caught.value.kind == "configuration"
    assert caught.value.authority == "data"


@pytest.mark.parametrize(
    ("status", "kind", "admin_status", "retryable"),
    [
        (401, "unauthorized", 401, False),
        (403, "forbidden", 403, False),
        (404, "not_found", 404, False),
        (409, "conflict", 409, False),
        (422, "invalid_request", 422, False),
        (500, "upstream_failure", 502, True),
        (503, "upstream_failure", 502, True),
    ],
)
async def test_hub_status_mapping(
    status: int, kind: str, admin_status: int, retryable: bool
) -> None:
    http_client = client(
        lambda _request: httpx.Response(status, json={"detail": "producer detail"})
    )
    try:
        subject = HubManagementClient(
            directory=directory(),  # type: ignore[arg-type]
            client=http_client,
            timeout_seconds=1,
        )
        with pytest.raises(AuthorityFailure) as caught:
            await subject.list_devices(
                owner_id="owner-1", authorization="Bearer operator"
            )
    finally:
        await http_client.aclose()
    assert caught.value.kind == kind
    assert caught.value.status_code == admin_status
    assert caught.value.retryable is retryable
    assert caught.value.upstream_status == status


async def test_timeout_is_unavailable_not_not_found() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    http_client = client(handler)
    try:
        subject = KernelMountClient(
            directory=directory(),  # type: ignore[arg-type]
            client=http_client,
            timeout_seconds=0.01,
        )
        with pytest.raises(AuthorityFailure) as caught:
            await subject.list_mounts(owner_id="owner-1")
    finally:
        await http_client.aclose()
    assert caught.value.kind == "unavailable"
    assert caught.value.retryable is True
    assert caught.value.status_code == 503


async def test_schema_drift_is_a_contract_violation() -> None:
    http_client = client(
        lambda _request: httpx.Response(
            200,
            json={
                "operation": "kernel.device-mount-page",
                "next_cursor": None,
                "mounts": [],
                "unexpected": "drift",
            },
        )
    )
    try:
        subject = KernelMountClient(
            directory=directory(),  # type: ignore[arg-type]
            client=http_client,
            timeout_seconds=1,
        )
        with pytest.raises(AuthorityFailure) as caught:
            await subject.list_mounts(owner_id="owner-1")
    finally:
        await http_client.aclose()
    assert caught.value.kind == "contract_violation"
    assert caught.value.status_code == 502


async def test_data_identity_mismatch_is_a_contract_violation() -> None:
    http_client = client(
        lambda _request: httpx.Response(
            200,
            json={
                "operation": "companion.identity",
                "companion_id": "different-companion",
                "owner_id": "owner-1",
                "lifecycle_state": "active",
            },
        )
    )
    try:
        subject = DataAuthorityClient(
            directory=directory(),  # type: ignore[arg-type]
            client=http_client,
            service_token=" admin-token ",
            timeout_seconds=1,
        )
        with pytest.raises(AuthorityFailure) as caught:
            await subject.get_companion("companion-1")
    finally:
        await http_client.aclose()
    assert caught.value.kind == "contract_violation"


async def test_kernel_page_cannot_cross_owner_scope() -> None:
    now = datetime.now(UTC).isoformat()
    http_client = client(
        lambda _request: httpx.Response(
            200,
            json={
                "operation": "kernel.device-mount-page",
                "next_cursor": None,
                "mounts": [
                    {
                        "operation": "kernel.device-mount",
                        "device_id": "device-1",
                        "owner_id": "owner-other",
                        "attached_companion_id": None,
                        "revision": 1,
                        "created_at": now,
                        "updated_at": now,
                        "request_id": "request-1",
                        "fingerprint": "sha256:" + "0" * 64,
                        "active": True,
                    }
                ],
            },
        )
    )
    try:
        subject = KernelMountClient(
            directory=directory(),  # type: ignore[arg-type]
            client=http_client,
            timeout_seconds=1,
        )
        with pytest.raises(AuthorityFailure) as caught:
            await subject.list_mounts(owner_id="owner-1")
    finally:
        await http_client.aclose()
    assert caught.value.kind == "contract_violation"


async def test_directory_rejects_contract_drift() -> None:
    payload = {
        "operation": "system.service-endpoint",
        "service_id": "kernel",
        "endpoint_id": "device-mount.http",
        "protocol": "http",
        "address": "http://kernel.test",
        "contract": "old.contract",
    }
    http_client = client(lambda _request: httpx.Response(200, json=payload))
    subject = SystemDirectoryClient(
        base_url="http://directory.test",
        timeout_seconds=1,
        client=http_client,
    )
    try:
        with pytest.raises(AuthorityFailure) as caught:
            await subject.resolve(
                service_id="kernel",
                endpoint_id="device-mount.http",
                required_contract=KERNEL_CONTRACT,
            )
    finally:
        await http_client.aclose()
    assert caught.value.kind == "contract_violation"


async def test_directory_maps_missing_or_not_ready_endpoint_to_unavailable() -> None:
    for status in (404, 503):
        http_client = client(lambda _request, status=status: httpx.Response(status))
        subject = SystemDirectoryClient(
            base_url="http://directory.test",
            timeout_seconds=1,
            client=http_client,
        )
        try:
            with pytest.raises(AuthorityFailure) as caught:
                await subject.resolve(
                    service_id="kernel",
                    endpoint_id="device-mount.http",
                    required_contract=KERNEL_CONTRACT,
                )
        finally:
            await http_client.aclose()
        assert caught.value.kind == "unavailable"
        assert caught.value.retryable is True


async def test_uds_directory_constructs_and_closes_owned_transport(
    tmp_path: Path,
) -> None:
    subject = SystemDirectoryClient(
        base_url="http://eidolond.local",
        timeout_seconds=1,
        uds_path=tmp_path / "eidolond.sock",
    )
    assert subject._owns_client is True
    await subject.close()
    assert subject._client.is_closed is True
