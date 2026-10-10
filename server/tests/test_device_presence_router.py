from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from eidolon_admin_server.app.management.device_presence_router import router


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["ok", "unavailable", "malformed"])
async def test_presence_requires_internal_auth_and_filters_owner(mode):
    async def provider(request):
        assert request.url.path == "/v1/device-channels/presence"
        if mode == "unavailable":
            return httpx.Response(503)
        if mode == "malformed":
            return httpx.Response(200, json={"bodies": {}})
        return httpx.Response(200, json={"bodies": [
            {"device_id": "mine", "owner_id": "owner-a", "on_channel": True},
            {"device_id": "other", "owner_id": "owner-b", "on_channel": True},
        ]})
    app = FastAPI()
    app.include_router(router)
    app.state.settings = SimpleNamespace(local_api_service_token="test-service-token")
    app.state.registry = {"channel-provider": SimpleNamespace(
        base_url="http://channel", upstream_prefix="", auth=SimpleNamespace(type="none"))}
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as upstream:
        app.state.http_client = upstream
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://admin") as client:
            path = "/internal/v1/management/device-presence?owner_id=owner-a"
            assert (await client.get(path)).status_code == 401
            result = await client.get(path, headers={"Authorization": "Bearer test-service-token"})
    if mode == "ok":
        assert result.status_code == 200
        assert [row["device_id"] for row in result.json()["bodies"]] == ["mine"]
    else:
        assert result.status_code == 503
