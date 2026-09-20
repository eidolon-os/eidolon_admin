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
    changed_setup_input_reason,
    existing_workspace,
    host_workspace_operation_id,
    resolve_workspace_setup,
    workspace_setup_detail,
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
        )
    assert caught.value.status_code == 409
    # The only way to reach this screen is a Host that showed a setup form
    # while already holding a Workspace, so the refusal names the one string
    # that would work rather than leaving it to be guessed.
    assert caught.value.reason is not None
    assert "Manson" in caught.value.reason


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


class _AbsentWorkspace:
    """A Data plane that has no operation under this Host's id."""

    def __init__(self) -> None:
        self.initialize_calls = 0
        self.get_calls = 0

    async def get(self, _operation_id: str) -> WorkspaceOperation:
        self.get_calls += 1
        raise WorkspaceSetupError(
            "Workspace operation does not exist", status_code=404
        )

    async def initialize(self, **_kwargs) -> WorkspaceOperation:
        self.initialize_calls += 1
        raise AssertionError("this fixture is asked to initialize by name")

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_a_host_with_no_data_workspace_reads_as_absent_rather_than_refusing() -> None:
    """The condition that stranded a real Host, and what it costs now.

    Bootstrap used to hold an Owner beside this, so a Data plane with no
    Workspace was a Host whose two halves disagreed: reading answered a bare
    404 and writing turned the same 404 into a 503, forever, for every phone.
    With one holder there is nothing to disagree with — Data has no Workspace,
    which is a state the contract can simply say.
    """

    operation_id = host_workspace_operation_id("ehost-56475aa75463474c0285")
    workspace = _AbsentWorkspace()

    assert (
        await existing_workspace(workspace, operation_id=operation_id) is None
    )
    assert workspace.get_calls == 1


@pytest.mark.asyncio
async def test_an_unbound_host_still_initializes_when_data_has_nothing() -> None:
    operation_id = host_workspace_operation_id("ehost-56475aa75463474c0285")
    payload = WorkspaceInitializeRequest(owner_display_name="Manson")
    created = WorkspaceOperation.model_validate(
        _workspace_response(operation_id, payload)
    )

    class FirstSetup(_AbsentWorkspace):
        async def initialize(self, **_kwargs) -> WorkspaceOperation:
            self.initialize_calls += 1
            return created

    workspace = FirstSetup()
    assert (
        await existing_workspace(
            workspace,  # type: ignore[arg-type]
            operation_id=operation_id,
        )
        is None
    )
    assert (
        await resolve_workspace_setup(
            workspace,  # type: ignore[arg-type]
            operation_id=operation_id,
            payload=payload,
        )
        == created
    )
    assert workspace.initialize_calls == 1


def test_only_a_tagged_refusal_reaches_a_person() -> None:
    """The App drops a bare string detail, deliberately, so this must not be one."""

    named = changed_setup_input_reason("Manson")
    tagged = workspace_setup_detail(
        WorkspaceSetupError(
            "This Host workspace was initialized with different setup input",
            status_code=409,
            reason=named,
        )
    )
    assert tagged == {"reason": named}
    # The Owner the Workspace is under, which is the one thing a person on
    # that screen needs and has never been shown.
    assert "Manson" in named
    # Under the App's own cap on what it will show.
    assert len(named) <= 300
    assert len(changed_setup_input_reason()) <= 300

    diagnostic = workspace_setup_detail(
        WorkspaceSetupError("Admin workspace control plane is unavailable")
    )
    assert diagnostic == "Admin workspace control plane is unavailable"


def test_workspace_authoring_is_forwarded_and_legacy_fingerprints_survive():
    import hashlib
    from eidolon_sdk.biz.persona import PersonaAuthoring, ConversationPreferences
    from eidolon_admin_server.local_api.workspace import WorkspaceSetupRequest

    legacy = {"owner_display_name": "Owner", "companion_display_name": "Eidolon"}
    expected = "sha256:" + hashlib.sha256(
        json.dumps(legacy, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert workspace_request_fingerprint(WorkspaceInitializeRequest(**legacy)) == expected
    setup = WorkspaceSetupRequest(
        **legacy,
        persona=PersonaAuthoring(character_portrait="第一位就是自己选的伙伴"),
        preferences=ConversationPreferences(response_length="brief"),
        source_preset_id="water", source_preset_revision="1",
    )
    payload = setup.to_admin()
    assert payload.persona == setup.persona
    assert payload.preferences == setup.preferences
    assert payload.source_preset_id == "water"
    assert workspace_request_fingerprint(payload) != expected
    changed = payload.model_copy(update={"preferences": ConversationPreferences(response_length="detailed")})
    assert workspace_request_fingerprint(changed) != workspace_request_fingerprint(payload)
