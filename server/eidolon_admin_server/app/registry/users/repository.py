"""Repository layer for Users.

Admin's SQLite registry is the only source of truth for user existence and
the project-wide enabled flag. The memory project consumes this table and
executes the resulting runtime state; it no longer owns a separate user
catalog.
"""
from __future__ import annotations

from typing import Any

from eidolon_sdk.http import (
    ServiceHTTPClient,
)

# ===== memory HTTP client ===================================================


class MemoryUserClient(ServiceHTTPClient):
    """HTTP wrapper over memory's user CRUD surface.

    Memory exposes these on its supervisor-embedded HTTP (default
    ``http://127.0.0.1:8019``, set via ``supervisor.admin_http_port``):

        GET    /api/admin/users
        GET    /api/admin/users/{user_id}
        POST   /api/admin/users
        DELETE /api/admin/users/{user_id}

    There is no PUT — memory's surface from 29.B.2 doesn't update meta.
    Admin's "update" is therefore limited to admin-owned fields
    (tenant_id, active_agent_id) — the orchestrator enforces that.
    """

    async def list_users(self) -> dict[str, Any]:
        """Returns the full envelope (users list + memory_available)."""
        r = await self._request("GET", "/api/admin/users")
        return r.json()

    async def get_user(self, user_id: str) -> dict[str, Any]:
        r = await self._request("GET", f"/api/admin/users/{user_id}")
        return r.json()

    async def create_user(
        self,
        *,
        user_id: str,
        enabled: bool = False,
        display_name: str | None = None,
        palace_path: str = "",
        consolidator: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "user_id": user_id,
            "enabled": enabled,
            "palace_path": palace_path,
        }
        if consolidator is not None:
            body["consolidator"] = consolidator
        # memory's CreateUserRequest has no display_name field — that's
        # an admin-side concept. We send only the fields memory accepts.
        # Create can block on memory's reconcile/palace init path.
        # Keep this longer than the
        # normal control-plane timeout so admin doesn't report failure
        # after memory already reconciled the registry.
        r = await self._request(
            "POST",
            "/api/admin/users",
            json=body,
            timeout=600.0,
        )
        return r.json()

    async def delete_user(self, user_id: str) -> dict[str, Any]:
        """Returns memory's response envelope (includes
        ``palace_trashed_to`` when applicable)."""
        r = await self._request(
            "DELETE",
            f"/api/admin/users/{user_id}",
            params={"purge": "true"},
            timeout=600.0,
        )
        return r.json()

    async def reconcile(self) -> dict[str, Any]:
        """Ask memory-supervisor to re-read the admin registry DB."""
        r = await self._request("POST", "/api/admin/reconcile", timeout=600.0)
        return r.json()

    async def rebuild_index(self, user_id: str) -> dict[str, Any]:
        """Start an async MemPalace vector-index rebuild for one user."""
        r = await self._request(
            "POST",
            f"/api/admin/users/{user_id}/memory/rebuild-index",
            ok_statuses=(202,),
            timeout=30.0,
        )
        return r.json()

    async def get_rebuild_index_job(self, job_id: str) -> dict[str, Any]:
        r = await self._request(
            "GET",
            f"/api/admin/memory/rebuild-index/{job_id}",
            timeout=30.0,
        )
        return r.json()

    async def list_rebuild_index_jobs(self, user_id: str) -> dict[str, Any]:
        r = await self._request(
            "GET",
            f"/api/admin/users/{user_id}/memory/rebuild-index",
            timeout=30.0,
        )
        return r.json()
