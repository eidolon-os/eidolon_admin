from __future__ import annotations

import httpx
import pytest
from eidolon_sdk.biz.system_data import CompanionRuntimeSnapshot

from eidolon_admin_server.app.control_plane.contracts import WorkspaceOperation
from eidolon_admin_server.local_api.runtime import (
    AdminOwnerRuntimeClient,
    WorkspaceRuntimeError,
    workspace_runtime_view,
)


def _runtime(
    *,
    owner_id: str = "owner-1",
    companion_id: str = "companion-1",
) -> CompanionRuntimeSnapshot:
    return CompanionRuntimeSnapshot.model_validate(
        {
            "contract_version": "1",
            "operation": "companion.runtime-snapshot",
            "owner_id": owner_id,
            "companion_id": companion_id,
            "lifecycle_state": "active",
            "runtime_config": {"internal": "not-for-mobile"},
            "memory_realm": {
                "realm_id": "realm-1",
                "lifecycle_state": "active",
            },
            "persona_genome": {
                "genome_id": "genome-current",
                "version": 2,
                "lifecycle_state": "committed",
                "schema_version": "eidolon.persona_genome",
                "genome_hash": "sha256:" + "a" * 64,
                "realizer_version": "1",
                "genome": {"private": "not-for-mobile"},
            },
        }
    )


def _workspace() -> WorkspaceOperation:
    return WorkspaceOperation.model_validate(
        {
            "contract_version": "1",
            "operation": "owner-workspace.initialize",
            "operation_id": "32c421a3-e0df-40f9-8f75-68745ae39d81",
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
                "persona_genome_id": "genome-origin",
                "memory_realm_id": "realm-1",
            },
        }
    )


@pytest.mark.asyncio
async def test_admin_runtime_client_uses_exact_owner_route_and_service_token() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.raw_path == (
            b"/api/control-plane/v1/owners/owner%2Fone/primary-runtime-snapshot"
        )
        assert request.headers["authorization"] == "Bearer local-service-token"
        return httpx.Response(
            200,
            json=_runtime(owner_id="owner/one").model_dump(mode="json"),
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    subject = AdminOwnerRuntimeClient(
        base_url="http://127.0.0.1:9000",
        service_token="local-service-token",
        timeout_seconds=1,
        client=http_client,
    )
    try:
        result = await subject.get_owner_primary_runtime("owner/one")
    finally:
        await http_client.aclose()
    assert result.owner_id == "owner/one"


def test_mobile_projection_excludes_raw_runtime_and_persona_payloads() -> None:
    view = workspace_runtime_view(
        workspace=_workspace(),
        runtime=_runtime(),
        bound_owner_id="owner-1",
    )

    payload = view.model_dump(mode="json")
    assert payload["state"] == "ready"
    assert payload["owner"]["display_name"] == "Manson"
    assert payload["primary_companion"]["companion_id"] == "companion-1"
    assert payload["persona"]["genome_id"] == "genome-current"
    assert payload["persona"]["version"] == 2
    assert "runtime_config" not in payload
    assert "genome" not in payload["persona"]


def test_mobile_projection_rejects_cross_owner_or_companion_state() -> None:
    with pytest.raises(WorkspaceRuntimeError) as owner_error:
        workspace_runtime_view(
            workspace=_workspace(),
            runtime=_runtime(owner_id="owner-other"),
            bound_owner_id="owner-1",
        )
    assert owner_error.value.status_code == 409

    with pytest.raises(WorkspaceRuntimeError) as companion_error:
        workspace_runtime_view(
            workspace=_workspace(),
            runtime=_runtime(companion_id="companion-other"),
            bound_owner_id="owner-1",
        )
    assert companion_error.value.status_code == 409
