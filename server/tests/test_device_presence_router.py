from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from eidolon_admin_server.app.management.device_presence_router import router


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["ok", "unavailable", "malformed"])
async def test_presence_requires_internal_auth_and_filters_device_domain(mode):
    async def provider(request):
        assert request.url.path == "/v1/device-channels/presence"
        if mode == "unavailable":
            return httpx.Response(503)
        if mode == "malformed":
            return httpx.Response(200, json={"bodies": {}})
        return httpx.Response(200, json={"bodies": [
            {"device_id": "mine", "owner_id": "owner_account", "device_ref": {"owner_domain_id": "owner-a"}, "on_channel": True},
            {"device_id": "other", "owner_id": "owner_account", "device_ref": {"owner_domain_id": "owner-b"}, "on_channel": True},
        ]})
    app = FastAPI()
    app.include_router(router)
    app.state.settings = SimpleNamespace(local_api_service_token="test-service-token")
    app.state.registry = {"channel-provider": SimpleNamespace(
        base_url="http://channel", upstream_prefix="", auth=SimpleNamespace(type="none"))}
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as upstream:
        app.state.http_client = upstream
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://admin") as client:
            path = "/internal/v1/management/device-presence?owner_domain_id=owner-a"
            assert (await client.get(path)).status_code == 401
            result = await client.get(path, headers={"Authorization": "Bearer test-service-token"})
    if mode == "ok":
        assert result.status_code == 200
        assert [row["device_id"] for row in result.json()["bodies"]] == ["mine"]
    else:
        assert result.status_code == 503


@pytest.mark.asyncio
async def test_local_api_client_sends_device_domain_not_business_owner():
    from eidolon_admin_server.local_api.management.backend import AdminManagementClient

    async def admin(request):
        assert request.url.path == "/api/internal/v1/management/device-presence"
        assert dict(request.url.params) == {"owner_domain_id": "device-domain-7"}
        assert request.headers["Authorization"] == "Bearer internal-test-token"
        return httpx.Response(200, json={"bodies": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(admin)) as http:
        client = AdminManagementClient(
            base_url="http://admin", service_token="internal-test-token",
            client=http, timeout_seconds=5,
        )
        assert await client.device_presence(owner_domain_id="device-domain-7") == {"bodies": []}
