from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from eidolon_admin_server.app.host_services.client import HostServiceClient
from eidolon_admin_server.app.host_services.errors import HostServiceError
from eidolon_admin_server.app.host_services.router import router as host_services_router

pytestmark = pytest.mark.asyncio

_STATUS = {
    "operation": "system.service-status",
    "service_id": "eidolon-hub",
    "required": True,
    "desired": {
        "service_id": "eidolon-hub",
        "enabled": True,
        "revision": 7,
        "updated_at": "2026-08-11T00:00:00Z",
    },
    "runtime_state": "ready",
    "detail": None,
    "observed_at": "2026-08-11T00:00:05Z",
    "endpoints": [
        {
            "operation": "system.service-endpoint",
            "service_id": "eidolon-hub",
            "endpoint_id": "device-authority.http",
            "protocol": "http",
            "address": "http://127.0.0.1:8082",
            "contract": "hub/device-authority.v1",
        }
    ],
}


# Shapes captured from a live eidolond (supervisord Mac, 2026-08-11). The
# service page carries no driver; health does.
_HEALTH = {
    "status": "ready",
    "scope": "machine",
    "owner_scoped": False,
    "host_driver": "supervisord",
}


def _client(handler) -> HostServiceClient:
    return HostServiceClient(
        base_url="http://127.0.0.1:8090",
        timeout_seconds=2.0,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def _app(client: HostServiceClient) -> FastAPI:
    app = FastAPI()
    app.state.host_services = client
    app.include_router(host_services_router, prefix="/api")
    return app


async def test_service_page_projects_only_the_facts_an_operator_acts_on() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json=_HEALTH)
        assert request.url.path == "/api/system/v1/services"
        return httpx.Response(200, json={"services": [_STATUS]})

    page = await _client(handler).list_services()

    # eidolond reports the real process manager; it is not assumed.
    assert page.driver == "supervisord"
    assert [item.service_id for item in page.services] == ["eidolon-hub"]
    service = page.services[0]
    assert service.enabled is True
    assert service.revision == 7
    assert service.runtime_state == "ready"
    assert service.endpoints[0].address == "http://127.0.0.1:8082"


async def test_mutation_carries_the_observed_revision_so_stale_views_lose() -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen.append(json.loads(request.content))
        assert request.url.path == "/api/system/v1/services/eidolon-hub/restart"
        return httpx.Response(
            200,
            json={
                "operation": "system.service-mutation-result",
                "state": {
                    "service_id": "eidolon-hub",
                    "enabled": True,
                    "revision": 8,
                    "updated_at": "2026-08-11T00:01:00Z",
                },
                "audit_position": 12,
                "replayed": False,
            },
        )

    result = await _client(handler).mutate(
        service_id="eidolon-hub", operation="restart", expected_revision=7
    )

    assert seen[0]["operation"] == "system.service.restart"
    assert seen[0]["expected_revision"] == 7
    assert seen[0]["request_id"]
    assert result.revision == 8
    assert result.audit_position == 12


async def test_a_lost_compare_and_swap_is_reported_as_a_conflict() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"detail": "revision mismatch"})

    with pytest.raises(HostServiceError) as error:
        await _client(handler).mutate(
            service_id="eidolon-hub", operation="restart", expected_revision=3
        )

    assert error.value.kind == "conflict"
    assert error.value.status_code == 409


async def test_an_unreachable_system_manager_is_not_reported_as_a_healthy_host() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(HostServiceError) as error:
        await _client(handler).list_services()

    assert error.value.kind == "unavailable"
    assert error.value.status_code == 503


async def test_a_response_missing_the_desired_state_is_refused() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json=_HEALTH)
        return httpx.Response(200, json={"services": [{"service_id": "x"}]})

    with pytest.raises(HostServiceError) as error:
        await _client(handler).list_services()

    assert error.value.kind == "invalid_response"


async def test_a_health_response_without_a_driver_is_refused() -> None:
    """Reporting a guessed driver would mislead an operator about the Host."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ready"})
        return httpx.Response(200, json={"services": [_STATUS]})

    with pytest.raises(HostServiceError) as error:
        await _client(handler).list_services()

    assert error.value.kind == "invalid_response"


async def test_rest_surface_exposes_control_on_both_hosts() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json=_HEALTH)
        if request.url.path.endswith("/restart"):
            return httpx.Response(
                200,
                json={
                    "operation": "system.service-mutation-result",
                    "state": {
                        "service_id": "eidolon-hub",
                        "enabled": True,
                        "revision": 8,
                        "updated_at": "2026-08-11T00:01:00Z",
                    },
                    "audit_position": 3,
                    "replayed": False,
                },
            )
        return httpx.Response(200, json={"services": [_STATUS]})

    app = _app(_client(handler))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://admin") as api:
        listed = await api.get("/api/host/services")
        restarted = await api.post(
            "/api/host/services/eidolon-hub/restart", json={"expected_revision": 7}
        )
        stale = await api.post("/api/host/services/eidolon-hub/restart", json={})

    assert listed.status_code == 200
    assert listed.json()["services"][0]["revision"] == 7
    assert restarted.status_code == 200
    assert restarted.json()["revision"] == 8
    # A caller that will not say what it saw cannot mutate.
    assert stale.status_code == 422


async def test_rest_surface_reports_a_missing_system_manager_instead_of_guessing() -> None:
    app = FastAPI()
    app.include_router(host_services_router, prefix="/api")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://admin") as api:
        response = await api.get("/api/host/services")

    assert response.status_code == 503


def test_a_unix_socket_host_gets_its_own_transport() -> None:
    """The Pi reaches eidolond over /run/eidolon/system.sock, not TCP."""

    client = HostServiceClient(
        base_url="http://eidolond",
        timeout_seconds=2.0,
        uds_path=Path("/run/eidolon/system.sock"),
    )

    transport = client._client._transport  # noqa: SLF001 - transport choice is the contract
    assert isinstance(transport, httpx.AsyncHTTPTransport)
    assert transport._pool._uds == "/run/eidolon/system.sock"  # noqa: SLF001


def test_admin_does_not_hand_a_tcp_pool_to_a_unix_socket_host() -> None:
    """Sharing the pooled TCP client would silently ignore the socket path."""

    from eidolon_admin_server.app.main import create_app
    from eidolon_admin_server.app.settings import get_settings

    settings = get_settings().model_copy(
        update={"system_directory_uds": Path("/run/eidolon/system.sock")}
    )
    app = create_app(settings=settings)

    assert app.state.host_services._client is not app.state.http_client  # noqa: SLF001
