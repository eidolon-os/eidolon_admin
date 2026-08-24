"""Admin ASGI component tests with controlled application ports."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
from eidolon_sdk.biz.system_data import CompanionRuntimeSnapshot

from eidolon_admin_server.app.control_plane.contracts import (
    BoundaryCapabilities,
    CompanionIdentity,
    DeviceRef,
    DeviceAdmissionResult,
    HubLifecycleStatus,
    KernelMount,
    KernelMountPage,
    WorkspaceInitializeRequest,
    WorkspaceOperation,
    WorkflowStep,
)
from eidolon_admin_server.app.control_plane.errors import AuthorityFailure
from eidolon_admin_server.app.settings import Settings

pytestmark = [pytest.mark.asyncio, pytest.mark.component]


class DataPort:
    failure: AuthorityFailure | None = None

    async def get_companion(self, companion_id: str) -> CompanionIdentity:
        if self.failure:
            raise self.failure
        return CompanionIdentity(
            operation="companion.identity",
            companion_id=companion_id,
            owner_id="owner-1",
            lifecycle_state="active",
        )


class StubControlPlane:
    def __init__(self) -> None:
        self.data = DataPort()
        self.workspace_failure: AuthorityFailure | None = None

    @staticmethod
    def capabilities() -> BoundaryCapabilities:
        return BoundaryCapabilities(
            supported=("data.companion-identity.read",),
            unavailable_without_producer_contract=("global-audit-projection",),
        )

    async def inventory(self, **_kwargs):
        raise AuthorityFailure("hub", "forbidden", "management scope denied", 403, 403)

    async def initialize_workspace(
        self,
        *,
        operation_id: str,
        payload: WorkspaceInitializeRequest,
    ) -> WorkspaceOperation:
        if self.workspace_failure:
            raise self.workspace_failure
        return _workspace_operation(operation_id, payload.owner_display_name)

    async def get_workspace_operation(self, operation_id: str) -> WorkspaceOperation:
        if self.workspace_failure:
            raise self.workspace_failure
        return _workspace_operation(operation_id, "Manson")

    async def get_owner_default_runtime(
        self,
        owner_id: str,
    ) -> CompanionRuntimeSnapshot:
        return CompanionRuntimeSnapshot.model_validate(
            {
                "contract_version": "1",
                "operation": "companion.runtime-snapshot",
                "owner_id": owner_id,
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
            }
        )

    async def list_owner_device_mounts(self, owner_id: str) -> KernelMountPage:
        now = datetime.now(UTC)
        return KernelMountPage(
            operation="kernel.device-mount-page",
            next_cursor=None,
            mounts=(
                KernelMount(
                    operation="kernel.device-mount",
                    device_id="device-mounted-1",
                    owner_id=owner_id,
                    device_ref=DeviceRef(
                        device_instance_id="device-mounted-1",
                        owner_domain_id=owner_id,
                        owner_domain_generation=1,
                        claim_generation=1,
                        trust_epoch=1,
                        accepted_manifest_digest="sha256:" + "a" * 64,
                    ),
                    attached_companion_id="companion-1",
                    revision=2,
                    created_at=now,
                    updated_at=now,
                    request_id="device-mount-read-fixture",
                    fingerprint="sha256:" + "0" * 64,
                    active=True,
                ),
            ),
        )

    async def admit_device(self, payload, **_kwargs) -> DeviceAdmissionResult:
        blocked = payload.request_id == "blocked"
        now = datetime.now(UTC)
        mounted = KernelMount(
            operation="kernel.device-mount",
            device_id=payload.device_id,
            owner_id=payload.owner_id,
            device_ref=DeviceRef(
                device_instance_id=payload.device_id,
                owner_domain_id=payload.owner_id,
                owner_domain_generation=1,
                claim_generation=1,
                trust_epoch=1,
                accepted_manifest_digest="sha256:" + "a" * 64,
            ),
            attached_companion_id=None,
            revision=1,
            created_at=now,
            updated_at=now,
            request_id=f"admin:{payload.request_id}:kernel-mount",
            fingerprint="sha256:" + "0" * 64,
            active=True,
        )
        return DeviceAdmissionResult(
            request_id=payload.request_id,
            outcome="blocked" if blocked else "retry_required",
            completed_stage="kernel_mounted",
            recovery=(
                "operator-action-required"
                if blocked
                else "retry-forward-same-request-id"
            ),
            steps=(
                WorkflowStep(name="hub_approval", state="committed"),
                WorkflowStep(name="kernel_mount", state="committed", revision=1),
                WorkflowStep(
                    name="companion_attachment",
                    state="failed",
                    failure=AuthorityFailure(
                        "kernel",
                        "conflict" if blocked else "upstream_failure",
                        "request_id payload mismatch"
                        if blocked
                        else "Data authority unavailable",
                        409 if blocked else 502,
                        409 if blocked else 503,
                        not blocked,
                    ).to_wire(),
                ),
            ),
            hub=HubLifecycleStatus(
                operation="device.lifecycle-status",
                device_id=payload.device_id,
                owner_id=payload.owner_id,
                lifecycle_state="approved",
            ),
            mount=mounted,
        )

    async def admit_controller_device(
        self,
        *,
        payload,
    ) -> DeviceAdmissionResult:
        now = datetime.now(UTC)
        mount = KernelMount(
            operation="kernel.device-mount",
            device_id=payload.device_id,
            owner_id=payload.owner_id,
            device_ref=DeviceRef(
                device_instance_id=payload.device_id,
                owner_domain_id=payload.owner_id,
                owner_domain_generation=1,
                claim_generation=1,
                trust_epoch=1,
                accepted_manifest_digest="sha256:" + "a" * 64,
            ),
            attached_companion_id=payload.companion_id,
            revision=2,
            created_at=now,
            updated_at=now,
            request_id="admin:approval:kernel-attach",
            fingerprint="sha256:" + "0" * 64,
            active=True,
        )
        return DeviceAdmissionResult(
            request_id=payload.request_id,
            outcome="completed",
            completed_stage="companion_attached",
            steps=(
                WorkflowStep(name="hub_approval", state="committed"),
                WorkflowStep(name="kernel_mount", state="committed", revision=1),
                WorkflowStep(
                    name="companion_attachment", state="committed", revision=2
                ),
            ),
            hub=HubLifecycleStatus(
                operation="device.lifecycle-status",
                device_id=payload.device_id,
                owner_id=payload.owner_id,
                lifecycle_state="approved",
            ),
            mount=mount,
        )


def _workspace_operation(operation_id: str, owner_name: str) -> WorkspaceOperation:
    marker = operation_id.replace("-", "")
    return WorkspaceOperation.model_validate(
        {
            "contract_version": "1",
            "operation": "owner-workspace.initialize",
            "operation_id": operation_id,
            "request_fingerprint": "sha256:" + "0" * 64,
            "status": "succeeded",
            "owner": {
                "owner_id": f"owner_{marker}",
                "display_name": owner_name,
                "lifecycle_state": "active",
            },
            "workspace": {
                "state": "ready",
                "primary_companion_id": f"c_{marker}",
                "persona_genome_id": f"g_{marker}_origin",
                "memory_realm_id": f"r_{marker}",
            },
        }
    )


#: The internal plane requires this Host's service credential on every route
#: (see test_service_plane_authentication.py). These tests are about what the
#: routes *answer*, so they present the credential and say nothing about it.
SERVICE_TOKEN = "local-api-secret"
SERVICE_HEADERS = {"Authorization": f"Bearer {SERVICE_TOKEN}"}


async def request(app, method: str, path: str, **kwargs) -> httpx.Response:
    app.state.control_plane = StubControlPlane()
    return await authenticated_request(app, method, path, **kwargs)


async def authenticated_request(app, method: str, path: str, **kwargs) -> httpx.Response:
    """Call a service-plane route with a caller it accepts.

    Kept separate from ``request`` so a test can install its own stub first;
    both send the credential, because a test that forgot it would fail on
    authentication and read as a broken contract.
    """
    app.state.settings = Settings(local_api_service_token=SERVICE_TOKEN)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://admin.test"
    ) as client:
        headers = {**SERVICE_HEADERS, **kwargs.pop("headers", {})}
        return await client.request(method, path, headers=headers, **kwargs)


async def test_capabilities_exposes_missing_producer_contracts(app) -> None:
    response = await request(app, "GET", "/api/control-plane/v1/capabilities")
    assert response.status_code == 200
    assert response.json()["admin_sqlite_authority"] is False
    assert response.json()["global_audit_projection_configured"] is False


async def test_process_health_is_independent_of_authority_availability(app) -> None:
    control_plane = StubControlPlane()
    control_plane.data.failure = AuthorityFailure(
        "data", "unavailable", "authority unreachable", 503, retryable=True
    )
    app.state.control_plane = control_plane
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://admin.test"
    ) as client:
        response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


async def test_data_not_found_is_not_rewritten_as_inactive(app) -> None:
    control_plane = StubControlPlane()
    control_plane.data.failure = AuthorityFailure(
        "data", "not_found", "companion not found", 404, 404
    )
    app.state.control_plane = control_plane
    response = await authenticated_request(
        app, "GET", "/api/control-plane/v1/companions/missing"
    )

    assert response.status_code == 404
    assert response.json()["detail"]["kind"] == "not_found"


async def test_workspace_onboarding_requires_exact_local_api_credential(app) -> None:
    app.state.control_plane = StubControlPlane()
    app.state.settings = Settings(local_api_service_token="local-api-secret")
    operation_id = "32c421a3-e0df-40f9-8f75-68745ae39d81"
    path = f"/api/control-plane/v1/workspace-onboarding/operations/{operation_id}"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://admin.test"
    ) as client:
        missing = await client.put(path, json={"owner_display_name": "Manson"})
        accepted = await client.put(
            path,
            headers={"Authorization": "Bearer local-api-secret"},
            json={"owner_display_name": "Manson"},
        )
        resumed = await client.get(
            path,
            headers={"Authorization": "Bearer local-api-secret"},
        )

    assert missing.status_code == 401
    assert accepted.status_code == 200
    assert resumed.status_code == 200
    assert accepted.json() == resumed.json()
    assert accepted.json()["workspace"]["state"] == "ready"


@pytest.mark.parametrize(
    ("failure", "expected_status"),
    [
        (AuthorityFailure("data", "forbidden", "bad credential", 403, 403), 403),
        (AuthorityFailure("data", "not_found", "missing", 404, 404), 404),
        (
            AuthorityFailure(
                "data", "unavailable", "workspace down", 503, retryable=True
            ),
            503,
        ),
        (
            AuthorityFailure("data", "contract_violation", "schema drift", 502, 200),
            502,
        ),
    ],
)
async def test_workspace_onboarding_preserves_upstream_failure_semantics(
    app,
    failure: AuthorityFailure,
    expected_status: int,
) -> None:
    control_plane = StubControlPlane()
    control_plane.workspace_failure = failure
    app.state.control_plane = control_plane
    app.state.settings = Settings(local_api_service_token="local-api-secret")
    operation_id = "32c421a3-e0df-40f9-8f75-68745ae39d81"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://admin.test"
    ) as client:
        response = await client.get(
            f"/api/control-plane/v1/workspace-onboarding/operations/{operation_id}",
            headers={"Authorization": "Bearer local-api-secret"},
        )

    assert response.status_code == expected_status
    assert response.json()["detail"]["kind"] == failure.kind


async def test_owner_runtime_requires_local_api_credential_and_derives_owner_path(
    app,
) -> None:
    app.state.control_plane = StubControlPlane()
    app.state.settings = Settings(local_api_service_token="local-api-secret")
    path = "/api/control-plane/v1/owners/owner-1/default-runtime-snapshot"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://admin.test"
    ) as client:
        missing = await client.get(path)
        accepted = await client.get(
            path,
            headers={"Authorization": "Bearer local-api-secret"},
        )

    assert missing.status_code == 401
    assert accepted.status_code == 200
    assert accepted.json()["owner_id"] == "owner-1"
    assert accepted.json()["companion_id"] == "companion-1"


async def test_owner_device_mounts_are_narrow_and_require_local_api_credential(
    app,
) -> None:
    app.state.control_plane = StubControlPlane()
    app.state.settings = Settings(local_api_service_token="local-api-secret")
    path = "/api/control-plane/v1/owners/owner-1/device-mounts"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://admin.test"
    ) as client:
        missing = await client.get(path)
        accepted = await client.get(
            path,
            headers={"Authorization": "Bearer local-api-secret"},
        )

    assert missing.status_code == 401
    assert accepted.status_code == 200
    assert accepted.json()["mounts"][0]["owner_id"] == "owner-1"
    assert accepted.json()["mounts"][0]["device_id"] == "device-mounted-1"


async def test_authority_unavailable_is_not_rewritten_as_not_found(app) -> None:
    control_plane = StubControlPlane()
    control_plane.data.failure = AuthorityFailure(
        "data", "unavailable", "authority unreachable", 503, retryable=True
    )
    app.state.control_plane = control_plane
    response = await authenticated_request(
        app, "GET", "/api/control-plane/v1/companions/companion-1"
    )

    assert response.status_code == 503
    assert response.json()["detail"]["kind"] == "unavailable"
    assert response.json()["detail"]["retryable"] is True


async def test_inventory_preserves_forbidden_status(app) -> None:
    response = await request(
        app,
        "GET",
        "/api/operator/v1/owners/owner-1/inventory",
        headers={"Authorization": "Bearer wrong-scope"},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["authority"] == "hub"


async def test_partial_workflow_returns_202_with_explicit_state(app) -> None:
    response = await request(
        app,
        "POST",
        "/api/operator/v1/workflows/device-admission",
        headers={"Authorization": "Bearer operator"},
        json={
            "request_id": "workflow-1",
            "owner_id": "owner-1",
            "device_id": "device-1",
            "companion_id": "companion-1",
            "expected_mount_revision": 0,
            "replace_existing_mount": False,
        },
    )
    assert response.status_code == 202
    body = response.json()
    assert body["completed_stage"] == "kernel_mounted"
    assert body["distributed_atomic"] is False
    assert body["recovery"] == "retry-forward-same-request-id"
    assert body["steps"][-1]["failure"]["upstream_status"] == 503


async def test_non_retryable_partial_workflow_returns_conflict(app) -> None:
    response = await request(
        app,
        "POST",
        "/api/operator/v1/workflows/device-admission",
        headers={"Authorization": "Bearer operator"},
        json={
            "request_id": "blocked",
            "owner_id": "owner-1",
            "device_id": "device-1",
            "companion_id": "companion-1",
        },
    )
    assert response.status_code == 409
    assert response.json()["outcome"] == "blocked"
    assert response.json()["recovery"] == "operator-action-required"


async def test_local_approval_workflow_requires_service_auth_and_matching_device_id(
    app,
) -> None:
    app.state.control_plane = StubControlPlane()
    app.state.settings = Settings(local_api_service_token="local-api-secret")
    path = "/api/control-plane/v1/local-device-admissions/device-1"
    payload = {
        "contract_version": "1",
        "request_id": "mobile-claim-1",
        "owner_id": "owner-derived",
        "controller_id": "ectrl-0123456789abcdefabcd",
        "device_id": "device-1",
        "companion_id": "companion-1",
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://admin.test"
    ) as client:
        missing = await client.put(path, json=payload)
        accepted = await client.put(
            path,
            headers={"Authorization": "Bearer local-api-secret"},
            json=payload,
        )
        injected_device = await client.put(
            path,
            headers={"Authorization": "Bearer local-api-secret"},
            json={**payload, "device_id": "another-device"},
        )

    assert missing.status_code == 401
    assert accepted.status_code == 200
    assert accepted.json()["hub"]["device_id"] == "device-1"
    assert injected_device.status_code == 409


@pytest.mark.parametrize(
    "path",
    [
        "/api/data/owners",
        "/api/devices",
        "/api/events",
        "/api/memory/users",
        "/api/onboarding/initialize",
        "/api/resolve/companion",
    ],
)
async def test_removed_cross_database_routes_are_unavailable(app, path: str) -> None:
    """Admin does not answer for what another component owns.

    /api/mission-control/snapshot used to be on this list and is not any more.
    It is not an authority for anything — it composes what the authorities
    answer — and it was removed because it read the product database, not
    because its subject moved. It goes through the HTTP clients now, and
    test_mission_control_answers_without_opening_a_database holds it there.
    """

    response = await request(app, "GET", path)
    assert response.status_code == 404


async def test_mission_control_answers_without_opening_a_database(app) -> None:
    """Reachable, and honest about what it cannot reach.

    Asked as a request rather than by reading main.py or walking app.routes:
    this FastAPI defers route materialisation, so an enumeration finds nothing
    and a source grep passes on an import line while the include_router call is
    missing. Both of those happened here before this test was written this way.
    """

    response = await request(app, "GET", "/api/mission-control/snapshot")

    assert response.status_code == 200
    body = response.json()
    sources = {row["source"]: row for row in body["source_status"]}
    # No Owner was named and no authority publishes a list of them, so it says
    # so instead of picking one.
    assert sources["data.owners"]["ok"] is False
    assert "ask for one Owner by id" in sources["data.owners"]["detail"]


async def test_invalid_request_is_rejected_before_orchestration(app) -> None:
    response = await request(
        app,
        "POST",
        "/api/operator/v1/workflows/device-admission",
        json={
            "request_id": "contains spaces",
            "owner_id": "owner-1",
            "device_id": "device-1",
        },
    )
    assert response.status_code == 422
