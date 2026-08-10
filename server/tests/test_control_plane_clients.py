"""Transport failure semantics for bounded-context HTTP adapters."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from eidolon_admin_server.app.control_plane.clients import (
    DATA_CONTRACT,
    DATA_RUNTIME_CONTRACT,
    DATA_WORKSPACE_CONTRACT,
    HUB_CONTRACT,
    KERNEL_CONTRACT,
    DataAuthorityClient,
    DataWorkspaceAuthorityClient,
    HubManagementClient,
    KernelMountClient,
)
from eidolon_admin_server.app.control_plane.contracts import (
    ServiceEndpoint,
    WorkspaceInitializeRequest,
)
from eidolon_admin_server.app.control_plane.directory import SystemDirectoryClient
from eidolon_admin_server.app.control_plane.errors import AuthorityFailure
from eidolon_admin_server.app.control_plane.workspace_policy import (
    workspace_request_fingerprint,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.component]


class StaticDirectory:
    def __init__(self, addresses: dict[tuple[str, str], tuple[str, str]]) -> None:
        self.addresses = addresses

    async def resolve(
        self, *, service_id: str, endpoint_id: str, required_contract: str
    ):
        address, contract = self.addresses[(service_id, endpoint_id)]
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
            ("data", "companion-authority.http"): (
                "http://data.test",
                DATA_CONTRACT,
            ),
            ("data", "companion-runtime-authority.http"): (
                "http://data.test",
                DATA_RUNTIME_CONTRACT,
            ),
            ("data-workspace", "workspace-authority.http"): (
                "http://workspace.test",
                DATA_WORKSPACE_CONTRACT,
            ),
            ("hub", "device-authority.http"): ("http://hub.test", HUB_CONTRACT),
            ("kernel", "device-mount.http"): (
                "http://kernel.test",
                KERNEL_CONTRACT,
            ),
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


async def test_data_client_reads_owner_runtime_through_its_declared_contract() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.raw_path == (
            b"/api/companion-authority/v1/owners/owner%2Fone/primary-runtime-snapshot"
        )
        assert request.headers["authorization"] == "Bearer admin-token"
        return httpx.Response(
            200,
            json={
                "contract_version": "1",
                "operation": "companion.runtime-snapshot",
                "owner_id": "owner/one",
                "companion_id": "companion-1",
                "lifecycle_state": "active",
                "runtime_config": {},
                "memory_realm": {
                    "realm_id": "realm-1",
                    "lifecycle_state": "active",
                },
                "persona_genome": {
                    "genome_id": "genome-1",
                    "version": 1,
                    "lifecycle_state": "committed",
                    "schema_version": "eidolon.persona_genome",
                    "genome_hash": "sha256:" + "a" * 64,
                    "realizer_version": "1",
                    "genome": {},
                },
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
        result = await subject.get_owner_primary_runtime("owner/one")
    finally:
        await http_client.aclose()
    assert result.companion_id == "companion-1"


async def test_data_runtime_precondition_is_preserved_as_domain_conflict() -> None:
    http_client = client(
        lambda _request: httpx.Response(
            412,
            json={"detail": "owner has no active primary companion"},
        )
    )
    try:
        subject = DataAuthorityClient(
            directory=directory(),  # type: ignore[arg-type]
            client=http_client,
            service_token="admin-token",
            timeout_seconds=1,
        )
        with pytest.raises(AuthorityFailure) as caught:
            await subject.get_owner_primary_runtime("owner-1")
    finally:
        await http_client.aclose()
    assert caught.value.kind == "conflict"
    assert caught.value.status_code == 409
    assert caught.value.upstream_status == 412


async def test_workspace_client_uses_write_endpoint_and_distinct_credential() -> None:
    operation_id = "32c421a3-e0df-40f9-8f75-68745ae39d81"
    payload = WorkspaceInitializeRequest(owner_display_name="Manson")

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PUT"
        assert request.url == httpx.URL(
            f"http://workspace.test/api/workspace-authority/v1/operations/{operation_id}"
        )
        assert request.headers["authorization"] == "Bearer workspace-token"
        assert json.loads(request.content) == {
            "owner_display_name": "Manson",
            "companion_display_name": "Eidolon",
        }
        return httpx.Response(
            200,
            json={
                "contract_version": "1",
                "operation": "owner-workspace.initialize",
                "operation_id": operation_id,
                "request_fingerprint": workspace_request_fingerprint(payload),
                "status": "succeeded",
                "owner": {
                    "owner_id": "owner_32c421a3e0df40f98f7568745ae39d81",
                    "display_name": "Manson",
                    "lifecycle_state": "active",
                },
                "workspace": {
                    "state": "ready",
                    "primary_companion_id": "c_32c421a3e0df40f98f7568745ae39d81",
                    "persona_genome_id": "g_32c421a3e0df40f98f7568745ae39d81_origin",
                    "memory_realm_id": "r_32c421a3e0df40f98f7568745ae39d81",
                },
            },
        )

    http_client = client(handler)
    try:
        subject = DataWorkspaceAuthorityClient(
            directory=directory(),  # type: ignore[arg-type]
            client=http_client,
            service_token=" workspace-token ",
            timeout_seconds=1,
        )
        result = await subject.initialize(
            operation_id=operation_id,
            payload=payload,
        )
    finally:
        await http_client.aclose()
    assert result.owner.owner_id == "owner_32c421a3e0df40f98f7568745ae39d81"


async def test_workspace_client_requires_its_own_write_credential() -> None:
    http_client = client(lambda _request: httpx.Response(500))
    try:
        subject = DataWorkspaceAuthorityClient(
            directory=directory(),  # type: ignore[arg-type]
            client=http_client,
            service_token="",
            timeout_seconds=1,
        )
        with pytest.raises(AuthorityFailure) as caught:
            await subject.get("32c421a3-e0df-40f9-8f75-68745ae39d81")
    finally:
        await http_client.aclose()
    assert caught.value.kind == "configuration"


@pytest.mark.parametrize(
    ("status", "kind", "admin_status", "retryable"),
    [
        (400, "invalid_request", 422, False),
        (401, "unauthorized", 401, False),
        (403, "forbidden", 403, False),
        (404, "not_found", 404, False),
        (409, "conflict", 409, False),
        (422, "invalid_request", 422, False),
        (500, "upstream_failure", 502, True),
        (503, "upstream_failure", 502, True),
    ],
)
async def test_workspace_status_mapping(
    status: int, kind: str, admin_status: int, retryable: bool
) -> None:
    http_client = client(
        lambda _request: httpx.Response(status, json={"detail": "workspace failure"})
    )
    try:
        subject = DataWorkspaceAuthorityClient(
            directory=directory(),  # type: ignore[arg-type]
            client=http_client,
            service_token="workspace-token",
            timeout_seconds=1,
        )
        with pytest.raises(AuthorityFailure) as caught:
            await subject.initialize(
                operation_id="32c421a3-e0df-40f9-8f75-68745ae39d81",
                payload=WorkspaceInitializeRequest(owner_display_name="Manson"),
            )
    finally:
        await http_client.aclose()
    assert caught.value.kind == kind
    assert caught.value.status_code == admin_status
    assert caught.value.retryable is retryable
    assert caught.value.upstream_status == status


async def test_workspace_response_fingerprint_mismatch_is_contract_violation() -> None:
    operation_id = "32c421a3-e0df-40f9-8f75-68745ae39d81"
    http_client = client(
        lambda _request: httpx.Response(
            200,
            json={
                "contract_version": "1",
                "operation": "owner-workspace.initialize",
                "operation_id": operation_id,
                "request_fingerprint": "sha256:" + "0" * 64,
                "status": "succeeded",
                "owner": {
                    "owner_id": "owner-1",
                    "display_name": "Manson",
                    "lifecycle_state": "active",
                },
                "workspace": {
                    "state": "ready",
                    "primary_companion_id": "companion-1",
                    "persona_genome_id": "genome-1",
                    "memory_realm_id": "realm-1",
                },
            },
        )
    )
    try:
        subject = DataWorkspaceAuthorityClient(
            directory=directory(),  # type: ignore[arg-type]
            client=http_client,
            service_token="workspace-token",
            timeout_seconds=1,
        )
        with pytest.raises(AuthorityFailure) as caught:
            await subject.initialize(
                operation_id=operation_id,
                payload=WorkspaceInitializeRequest(owner_display_name="Manson"),
            )
    finally:
        await http_client.aclose()
    assert caught.value.kind == "contract_violation"


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


async def test_hub_approval_uses_exact_contract_and_confirms_device_id() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.raw_path == (
            b"/api/device-management/v1/devices/device-1/approval"
        )
        assert request.headers["authorization"] == "Bearer owner-jwt"
        assert json.loads(request.content) == {
            "operation": "device.approval",
            "request_id": "admin:approval:hub-approve",
            "owner_id": "owner-1",
        }
        return httpx.Response(
            200,
            json={
                "operation": "device.lifecycle-status",
                "device_id": "device-1",
                "owner_id": "owner-1",
                "lifecycle_state": "approved",
            },
        )

    http_client = client(handler)
    try:
        subject = HubManagementClient(
            directory=directory(),  # type: ignore[arg-type]
            client=http_client,
            timeout_seconds=1,
        )
        result = await subject.approve(
            device_id="device-1",
            owner_id="owner-1",
            request_id="admin:approval:hub-approve",
            authorization="Bearer owner-jwt",
        )
    finally:
        await http_client.aclose()

    assert result.device_id == "device-1"


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
