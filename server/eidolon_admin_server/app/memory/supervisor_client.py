"""HTTP client for memory-supervisor maintenance endpoints."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx
from eidolon_sdk.core.http import ServiceHTTPClient


class MemorySupervisorClient(ServiceHTTPClient):
    async def reconcile(self) -> dict[str, Any]:
        """Force the supervisor to re-read the admin registry and start/stop
        workers to match. Used after a realm is removed from the registry so
        its orphaned worker is reaped before its palace is trashed."""
        r = await self._request("POST", "/api/admin/reconcile", timeout=30.0)
        return r.json()

    async def list_realms(self) -> dict[str, Any]:
        r = await self._request("GET", "/api/admin/realms", timeout=15.0)
        return r.json()

    async def rebuild_index(self, memory_realm_id: str) -> dict[str, Any]:
        realm = quote(memory_realm_id, safe="")
        r = await self._request(
            "POST",
            f"/api/admin/realms/{realm}/memory/rebuild-index",
            ok_statuses=(202,),
            timeout=30.0,
        )
        return r.json()

    async def get_rebuild_index_job(self, job_id: str) -> dict[str, Any]:
        r = await self._request(
            "GET",
            f"/api/admin/memory/rebuild-index/{quote(job_id, safe='')}",
            timeout=30.0,
        )
        return r.json()

    async def list_rebuild_index_jobs(self, memory_realm_id: str) -> dict[str, Any]:
        realm = quote(memory_realm_id, safe="")
        r = await self._request(
            "GET",
            f"/api/admin/realms/{realm}/memory/rebuild-index",
            timeout=30.0,
        )
        return r.json()


def memory_supervisor_base_url() -> str:
    import os

    host = os.environ.get("EIDOLON_MEMORY_SUPERVISOR_HTTP_HOST", "127.0.0.1").strip()
    port = os.environ.get("EIDOLON_MEMORY_SUPERVISOR_HTTP_PORT", "8019").strip()
    return f"http://{host}:{port}"


def build_memory_supervisor_client(http_client: httpx.AsyncClient) -> MemorySupervisorClient:
    return MemorySupervisorClient(http_client, memory_supervisor_base_url())
