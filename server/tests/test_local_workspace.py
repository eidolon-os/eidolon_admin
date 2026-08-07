from __future__ import annotations

import json
from uuid import UUID

import httpx
import pytest

from eidolon_admin_server.app.control_plane.contracts import (
    WorkspaceInitializeRequest,
    WorkspaceOperation,
)
from eidolon_admin_server.app.control_plane.workspace_policy import (
    workspace_request_fingerprint,
)
from eidolon_admin_server.bootstrap.config import BootstrapMode, BootstrapSettings
from eidolon_admin_server.local_api.config import (
    LocalApiSettings,
    load_local_api_settings,
)
from eidolon_admin_server.local_api.workspace import (
    AdminWorkspaceClient,
    WorkspaceSetupError,
    host_workspace_operation_id,
    resolve_workspace_setup,
)


def _workspace_response(
    operation_id: str,
    payload: WorkspaceInitializeRequest | None = None,
) -> dict:
    resolved = payload or WorkspaceInitializeRequest(owner_display_name="Manson")
    marker = operation_id.replace("-", "")
    return {
        "contract_version": "1",
        "operation": "owner-workspace.initialize",
        "operation_id": operation_id,
        "request_fingerprint": workspace_request_fingerprint(resolved),
        "status": "succeeded",
        "owner": {
            "owner_id": f"owner_{marker}",
            "display_name": resolved.owner_display_name,
            "lifecycle_state": "active",
        },
        "workspace": {
            "state": "ready",
            "primary_companion_id": f"c_{marker}",
            "persona_genome_id": f"g_{marker}_origin",
            "memory_realm_id": f"r_{marker}",
        },
    }


def test_host_workspace_operation_is_stable_and_host_scoped() -> None:
    first = host_workspace_operation_id("ehost-56475aa75463474c0285")
    assert first == host_workspace_operation_id("ehost-56475aa75463474c0285")
    assert first != host_workspace_operation_id("ehost-0123456789abcdefabcd")
    assert UUID(first).version == 5


def test_local_api_rejects_non_loopback_admin_origin(tmp_path) -> None:
    with pytest.raises(ValueError, match="loopback HTTP origin"):
        load_local_api_settings(
            {
                "EIDOLON_BOOTSTRAP_MODE": "development",
                "EIDOLON_BOOTSTRAP_STATE_DIR": str(tmp_path / "state"),
                "EIDOLON_BOOTSTRAP_RUNTIME_DIR": str(tmp_path / "run"),
                "EIDOLON_LOCAL_API_ADMIN_BASE_URL": "https://admin.example.com",
            }
        )


@pytest.mark.asyncio
async def test_admin_workspace_client_calls_only_the_exact_loopback_route() -> None:
    operation_id = host_workspace_operation_id("ehost-56475aa75463474c0285")

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PUT"
        assert request.url == httpx.URL(
            "http://127.0.0.1:9000/api/control-plane/v1/"
            f"workspace-onboarding/operations/{operation_id}"
        )
        assert request.headers["authorization"] == "Bearer local-service-token"
        assert json.loads(request.content) == {
            "owner_display_name": "Manson",
            "companion_display_name": "Eidolon",
        }
        return httpx.Response(200, json=_workspace_response(operation_id))

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    subject = AdminWorkspaceClient(
        base_url="http://127.0.0.1:9000",
        service_token="local-service-token",
        timeout_seconds=1,
        client=http_client,
    )
    try:
        result = await subject.initialize(
            operation_id=operation_id,
            payload=WorkspaceInitializeRequest(owner_display_name="Manson"),
        )
    finally:
        await http_client.aclose()
    assert result.workspace.state == "ready"


@pytest.mark.asyncio
async def test_admin_workspace_client_does_not_downgrade_conflict() -> None:
    operation_id = host_workspace_operation_id("ehost-56475aa75463474c0285")
    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                409, json={"detail": "fingerprint mismatch"}
            )
        )
    )
    subject = AdminWorkspaceClient(
        base_url="http://127.0.0.1:9000",
        service_token="local-service-token",
        timeout_seconds=1,
        client=http_client,
    )
    try:
        with pytest.raises(WorkspaceSetupError) as caught:
            await subject.initialize(
                operation_id=operation_id,
                payload=WorkspaceInitializeRequest(owner_display_name="Changed"),
            )
    finally:
        await http_client.aclose()
    assert caught.value.status_code == 409


@pytest.mark.asyncio
async def test_admin_workspace_client_reports_an_absent_operation() -> None:
    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(404))
    )
    subject = AdminWorkspaceClient(
        base_url="http://127.0.0.1:9000",
        service_token="local-service-token",
        timeout_seconds=1,
        client=http_client,
    )
    try:
        with pytest.raises(WorkspaceSetupError) as caught:
            await subject.get(host_workspace_operation_id("ehost-56475aa75463474c0285"))
    finally:
        await http_client.aclose()
    assert caught.value.status_code == 404


@pytest.mark.asyncio
async def test_setup_resumes_a_completed_operation_before_owner_binding() -> None:
    operation_id = host_workspace_operation_id("ehost-56475aa75463474c0285")
    payload = WorkspaceInitializeRequest(owner_display_name="Manson")
    result = WorkspaceOperation.model_validate(
        _workspace_response(operation_id, payload)
    )

    class ExistingWorkspace:
        initialize_calls = 0

        async def get(self, requested_operation_id: str) -> WorkspaceOperation:
            assert requested_operation_id == operation_id
            return result

        async def initialize(self, **_kwargs) -> WorkspaceOperation:
            self.initialize_calls += 1
            raise AssertionError("existing operation must be resumed")

        async def close(self) -> None:
            return None

    workspace = ExistingWorkspace()
    resumed = await resolve_workspace_setup(
        workspace,  # type: ignore[arg-type]
        operation_id=operation_id,
        payload=payload,
        bound_owner_id=None,
    )
    assert resumed == result
    assert workspace.initialize_calls == 0


@pytest.mark.asyncio
async def test_setup_rejects_changed_input_before_owner_binding() -> None:
    operation_id = host_workspace_operation_id("ehost-56475aa75463474c0285")
    original = WorkspaceInitializeRequest(owner_display_name="Manson")
    result = WorkspaceOperation.model_validate(
        _workspace_response(operation_id, original)
    )

    class ExistingWorkspace:
        async def get(self, _operation_id: str) -> WorkspaceOperation:
            return result

        async def initialize(self, **_kwargs) -> WorkspaceOperation:
            raise AssertionError("a completed operation must not be reinitialized")

        async def close(self) -> None:
            return None

    with pytest.raises(WorkspaceSetupError) as caught:
        await resolve_workspace_setup(
            ExistingWorkspace(),  # type: ignore[arg-type]
            operation_id=operation_id,
            payload=WorkspaceInitializeRequest(owner_display_name="Changed"),
            bound_owner_id=None,
        )
    assert caught.value.status_code == 409


def test_local_api_settings_keep_admin_transport_separate(tmp_path) -> None:
    bootstrap = BootstrapSettings(
        mode=BootstrapMode.DEVELOPMENT,
        state_dir=tmp_path / "state",
        runtime_dir=tmp_path / "run",
        control_socket=tmp_path / "run/control.sock",
        ble_service_uuid="179e2e95-b1ee-5aa5-8dcf-7519b6c7ac52",
    )
    settings = LocalApiSettings(
        bootstrap=bootstrap,
        admin_service_token="separate-local-service-token",
    )
    assert settings.admin_base_url == "http://127.0.0.1:9000"
    assert settings.admin_service_token != ""
