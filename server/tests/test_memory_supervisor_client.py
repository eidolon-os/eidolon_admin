from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from eidolon_admin_server.app.memory.supervisor_client import MemorySupervisorClient

pytestmark = pytest.mark.asyncio


async def test_reconcile_timeout_covers_cold_realm_start(monkeypatch) -> None:
    client = MemorySupervisorClient(httpx.AsyncClient(), "http://memory-supervisor")
    calls: list[dict] = []

    async def fake_request(method, path, **kwargs):
        calls.append({"method": method, "path": path, **kwargs})
        return SimpleNamespace(json=lambda: {"ok": True})

    monkeypatch.setattr(client, "_request", fake_request)

    assert await client.reconcile() == {"ok": True}
    assert calls == [
        {
            "method": "POST",
            "path": "/api/admin/reconcile",
            "timeout": 120.0,
        }
    ]

    await client._http.aclose()
