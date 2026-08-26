"""Transport failure semantics for bounded-context HTTP adapters."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from eidolon_sdk.device_foundation.v1 import (
    BusinessOwnerId,
    ClaimQuery,
    ClaimState,
    DecideEnrollment,
    DeviceRef,
    EnrollmentProposalQuery,
    EnrollmentProposalState,
    ManifestRef,
    OwnerDomainId,
)

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

from eidolon_sdk.device_foundation.v1.testing import named_device_instance_id

# Tests name the device they mean; the name becomes a real device
# instance id, which is a digest of a key and never a chosen string.
_DEVICE_1 = named_device_instance_id("device-1")

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


ROSTER_PAGE = {
    "contract_version": "1",
    "operation": "companion.roster-page",
    "owner_id": "owner one",
    "default_companion_id": "companion-a",
    "companions": [
        {
            "companion_id": "companion-a",
            "display_name": "小忆",
            "kind": "standard",
            "lifecycle_state": "active",
            "revision": 2,
            "created_at": "2026-08-24T09:30:00+00:00",
            "updated_at": "2026-08-24T09:30:00+00:00",
        }
    ],
    "next_cursor": "opaque",
}


async def test_roster_is_read_owner_scoped_with_the_cursor_untouched() -> None:
    """The Owner is in the path, so scope is the authority's filter, not ours.

    A client-side filter over an unscoped list is the version of this that
    leaks: it works until someone adds a code path that forgets to apply it.
    """

    seen: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.raw_path.split(b"?")[0]
        seen["cursor"] = request.url.params.get("cursor")
        assert request.headers["authorization"] == "Bearer admin-token"
        return httpx.Response(200, json=ROSTER_PAGE)

    http_client = client(handler)
    try:
        subject = DataAuthorityClient(
            directory=directory(),  # type: ignore[arg-type]
            client=http_client,
            service_token="admin-token",
            timeout_seconds=1,
        )
        page = await subject.list_owner_companions("owner one", cursor="page-2")
    finally:
        await http_client.aclose()

    assert seen["path"] == b"/api/companion-authority/v1/owners/owner%20one/companions"
    assert seen["cursor"] == "page-2"
    assert page.default_companion_id == "companion-a"
    assert page.next_cursor == "opaque"


async def test_no_cursor_means_no_cursor_parameter() -> None:
    """Not an empty string: the authority's own default page start.

    Sending ``cursor=`` would ask it to decode nothing into a position.
    """

    seen: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["query"] = request.url.query
        return httpx.Response(200, json=ROSTER_PAGE)

    http_client = client(handler)
    try:
        subject = DataAuthorityClient(
            directory=directory(),  # type: ignore[arg-type]
            client=http_client,
            service_token="admin-token",
            timeout_seconds=1,
        )
        await subject.list_owner_companions("owner one")
    finally:
        await http_client.aclose()

    assert seen["query"] == b""


async def test_a_roster_for_another_owner_is_a_contract_violation() -> None:
    """Cheap to check, and the failure it catches is a cross-Owner leak."""
    http_client = client(
        lambda _request: httpx.Response(
            200, json={**ROSTER_PAGE, "owner_id": "someone-else"}
        )
    )
    try:
        subject = DataAuthorityClient(
            directory=directory(),  # type: ignore[arg-type]
            client=http_client,
            service_token="admin-token",
            timeout_seconds=1,
        )
        with pytest.raises(AuthorityFailure) as failure:
            await subject.list_owner_companions("owner one")
    finally:
        await http_client.aclose()

    assert failure.value.kind == "contract_violation"


async def test_a_roster_row_claiming_the_default_is_refused() -> None:
    """Strict parsing is the guard against the fact arriving twice.

    If a producer ever added ``is_default`` per row, this consumer must fail
    loudly rather than quietly carry two answers to one question.
    """
    row = {**ROSTER_PAGE["companions"][0], "is_default": True}
    http_client = client(
        lambda _request: httpx.Response(200, json={**ROSTER_PAGE, "companions": [row]})
    )
    try:
        subject = DataAuthorityClient(
            directory=directory(),  # type: ignore[arg-type]
            client=http_client,
            service_token="admin-token",
            timeout_seconds=1,
        )
        with pytest.raises(AuthorityFailure) as failure:
            await subject.list_owner_companions("owner one")
    finally:
        await http_client.aclose()

    assert failure.value.kind == "contract_violation"


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
                "kind": "standard",
                "revision": 1,
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
            b"/api/companion-authority/v1/owners/owner%2Fone/default-runtime-snapshot"
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
        result = await subject.get_owner_default_runtime("owner/one")
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
            await subject.get_owner_default_runtime("owner-1")
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
            await subject.list_claims(
                query=ClaimQuery(
                    owner_domain_id=OwnerDomainId("owner-1"),
                    states=(ClaimState.ACTIVE,),
                    cursor=None,
                    limit=20,
                ),
                authorization="Bearer operator",
            )
    finally:
        await http_client.aclose()
    assert caught.value.kind == kind
    assert caught.value.status_code == admin_status
    assert caught.value.retryable is retryable
    assert caught.value.upstream_status == status


async def test_hub_decision_uses_exact_canonical_contract() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.raw_path == (
            b"/api/admission/v1/enrollments/enrollment-1/decisions"
        )
        assert request.headers["authorization"] == "Bearer owner-jwt"
        body = json.loads(request.content)
        assert body["command_id"] == "decide-enrollment-1"
        assert body["correlation_id"] == "admission-intent-1"
        assert body["target_owner_domain_id"] == "owner-1"
        return httpx.Response(
            200,
            json={
                "decision_id": "decision-1",
                "decision": "approve",
                "decided_by": {
                    "principal_id": "ectrl-0123456789abcdef0123",
                    "principal_type": "controller",
                    "owner_domain_id": "owner-1",
                    "granted_scopes": ["device.read", "device.claim.approve"],
                    "authentication_strength": "software",
                },
                "decided_at": datetime.now(UTC).isoformat(),
                "proposal_revision": 1,
            },
        )

    http_client = client(handler)
    try:
        subject = HubManagementClient(
            directory=directory(),  # type: ignore[arg-type]
            client=http_client,
            timeout_seconds=1,
        )
        result = await subject.decide_enrollment(
            command=DecideEnrollment(
                enrollment_id="enrollment-1",
                expected_proposal_revision=1,
                decision="approve",
                target_owner_domain_id=OwnerDomainId("owner-1"),
                target_business_owner_id=BusinessOwnerId("owner_account_1"),
                reviewed_manifest_ref=ManifestRef(
                    manifest_id="manifest-1",
                    revision=1,
                    digest="sha256:" + "a" * 64,
                ),
            ),
            command_id="decide-enrollment-1",
            correlation_id="admission-intent-1",
            authorization="Bearer owner-jwt",
        )
    finally:
        await http_client.aclose()

    assert result.decision_id == "decision-1"


async def test_hub_revocation_uses_exact_canonical_claim_command() -> None:
    device_ref = DeviceRef(
        device_instance_id=_DEVICE_1,
        owner_domain_id=OwnerDomainId("owner-1"),
        owner_domain_generation=2,
        claim_generation=3,
        trust_epoch=4,
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.raw_path == f"/api/admission/v1/claims/{_DEVICE_1}:revoke".encode()
        assert request.headers["authorization"] == "Bearer owner-jwt"
        body = json.loads(request.content)
        assert body == {
            "operation": "device.claim-revocation",
            "command_id": "revoke-1",
            "correlation_id": "removal-1",
            "device_ref": device_ref.model_dump(mode="json"),
            "reason": "owner-requested",
        }
        return httpx.Response(
            200,
            json={
                "operation": "device.claim-revocation-result",
                "command_id": "revoke-1",
                "outcome": "committed",
                "device_ref": device_ref.model_dump(mode="json"),
                "aggregate_revision": 5,
                "lifecycle_state": "revoked",
                "event_id": "claim-event-1",
                "occurred_at": datetime.now(UTC).isoformat(),
            },
        )

    http_client = client(handler)
    try:
        subject = HubManagementClient(
            directory=directory(),  # type: ignore[arg-type]
            client=http_client,
            timeout_seconds=1,
        )
        result = await subject.revoke(
            device_ref=device_ref,
            reason="owner-requested",
            command_id="revoke-1",
            correlation_id="removal-1",
            authorization="Bearer owner-jwt",
        )
    finally:
        await http_client.aclose()

    assert result.event_id == "claim-event-1"


async def test_device_erase_status_uses_source_event_and_full_generation() -> None:
    device_ref = DeviceRef(
        device_instance_id=_DEVICE_1,
        owner_domain_id=OwnerDomainId("owner-1"),
        owner_domain_generation=2,
        claim_generation=3,
        trust_epoch=4,
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == (
            f"/api/device-control/v1/owners/owner-1/devices/{_DEVICE_1}/erase-operations"
        )
        assert dict(request.url.params) == {
            "source_claim_event_id": "claim-event-1",
            "owner_domain_generation": "2",
            "claim_generation": "3",
            "trust_epoch": "4",
        }
        assert request.headers["authorization"] == "Bearer owner-jwt"
        now = datetime.now(UTC)
        return httpx.Response(
            200,
            json={
                "contract": "eidolon.device-foundation.device-operation-status",
                "contract_version": "1.0",
                "operation_id": "erase-operation-1",
                "operation_type": "device-local.erase",
                "request_fingerprint": "sha256:" + "a" * 64,
                "device_ref": device_ref.model_dump(mode="json"),
                "created_at": now.isoformat(),
                "deadline": (now + timedelta(days=1)).isoformat(),
                "state": "pending",
                "attempt_count": 0,
                "terminal_result": None,
            },
        )

    http_client = client(handler)
    try:
        subject = HubManagementClient(
            directory=directory(),  # type: ignore[arg-type]
            client=http_client,
            timeout_seconds=1,
        )
        result = await subject.get_device_control_operation(
            device_ref=device_ref,
            source_claim_event_id="claim-event-1",
            authorization="Bearer owner-jwt",
        )
    finally:
        await http_client.aclose()

    assert result.operation_id == "erase-operation-1"
    assert result.device_ref == device_ref


async def test_enrollment_query_preserves_recoverable_states() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == ("/api/admission/v1/enrollments")
        assert dict(request.url.params) == {
            "limit": "100",
            "states": "pending_review,approved_awaiting_handoff,grant_delivered",
        }
        return httpx.Response(
            200,
            json={
                "owner_domain_id": "owner-1",
                "items": [],
                "next_cursor": None,
                "observed_at": datetime.now(UTC).isoformat(),
            },
        )

    http_client = client(handler)
    try:
        subject = HubManagementClient(
            directory=directory(),  # type: ignore[arg-type]
            client=http_client,
            timeout_seconds=1,
        )
        await subject.list_enrollment_recovery(
            query=EnrollmentProposalQuery(
                owner_domain_id=OwnerDomainId("owner-1"),
                states=(
                    EnrollmentProposalState.PENDING_REVIEW,
                    EnrollmentProposalState.APPROVED_AWAITING_HANDOFF,
                    EnrollmentProposalState.GRANT_DELIVERED,
                ),
                cursor=None,
                limit=100,
            ),
            authorization="Bearer admin",
        )
    finally:
        await http_client.aclose()


async def test_operator_reads_use_the_hub_credential_scope_directly() -> None:
    seen: list[tuple[str, dict[str, str]]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer operator-jwt"
        seen.append((request.url.path, dict(request.url.params)))
        now = datetime.now(UTC).isoformat()
        if request.url.path.endswith("/enrollments"):
            return httpx.Response(
                200,
                json={
                    "owner_domain_id": "owner-domain-1",
                    "items": [],
                    "next_cursor": None,
                    "observed_at": now,
                },
            )
        return httpx.Response(
            200,
            json={
                "owner_domain_id": "owner-domain-1",
                "items": [],
                "next_cursor": None,
                "observed_at": now,
            },
        )

    http_client = client(handler)
    try:
        subject = HubManagementClient(
            directory=directory(),  # type: ignore[arg-type]
            client=http_client,
            timeout_seconds=1,
        )
        await subject.list_authorized_enrollments(
            authorization="Bearer operator-jwt",
            states=("pending_review", "grant_acknowledged"),
        )
        await subject.list_authorized_claims(
            authorization="Bearer operator-jwt",
        )
    finally:
        await http_client.aclose()

    assert seen == [
        (
            "/api/admission/v1/enrollments",
            {"states": "pending_review,grant_acknowledged", "limit": "200"},
        ),
        (
            "/api/admission/v1/claims",
            {"states": "active,suspended,revoked", "limit": "200"},
        ),
    ]


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
                "kind": "standard",
                "revision": 1,
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
                        "device_id": _DEVICE_1,
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
