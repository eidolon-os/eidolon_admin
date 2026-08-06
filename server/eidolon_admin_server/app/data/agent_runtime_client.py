"""Control-plane client for the Agent-owned runtime authority."""

from __future__ import annotations

from typing import Any

import httpx


class AgentRuntimeUnavailable(RuntimeError):
    pass


class AgentRuntimeAdminClient:
    def __init__(self, http_client: httpx.AsyncClient, base_url: str) -> None:
        self._http = http_client
        self._base_url = base_url.rstrip("/")

    @property
    def configured(self) -> bool:
        return bool(self._base_url)

    async def list_conversations(
        self, owner_id: str, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        body = await self._request(
            "GET",
            "/conversations",
            params={"owner_id": owner_id, "limit": limit},
        )
        rows = body.get("conversations") if isinstance(body, dict) else None
        return [row for row in rows or [] if isinstance(row, dict)]

    async def list_jobs(
        self, owner_id: str, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        body = await self._request(
            "GET",
            "/long-tasks",
            params={"owner_id": owner_id, "limit": limit},
        )
        rows = body.get("tasks") if isinstance(body, dict) else None
        return [row for row in rows or [] if isinstance(row, dict)]

    async def delete_owner_runtime(self, owner_id: str) -> dict[str, Any]:
        body = await self._request("DELETE", f"/owners/{owner_id}/data")
        if not isinstance(body, dict) or not bool(body.get("deleted")):
            raise AgentRuntimeUnavailable(
                f"Agent runtime delete did not confirm owner {owner_id!r}"
            )
        return body

    async def delete_companion_runtime(
        self, owner_id: str, companion_id: str
    ) -> dict[str, Any]:
        body = await self._request(
            "DELETE",
            f"/owners/{owner_id}/companions/{companion_id}/data",
        )
        if not isinstance(body, dict) or not bool(body.get("deleted")):
            raise AgentRuntimeUnavailable(
                f"Agent runtime delete did not confirm companion {companion_id!r}"
            )
        return body

    async def cancel_job(self, owner_id: str, job_id: str) -> dict[str, Any]:
        body = await self._request(
            "POST",
            f"/long-tasks/{job_id}/cancel",
            params={"owner_id": owner_id},
        )
        if not isinstance(body, dict):
            raise AgentRuntimeUnavailable("Agent returned an invalid cancelled job")
        return body

    async def retry_job(self, owner_id: str, job_id: str) -> dict[str, Any]:
        body = await self._request(
            "POST",
            f"/long-tasks/{job_id}/retry",
            params={"owner_id": owner_id},
        )
        if not isinstance(body, dict):
            raise AgentRuntimeUnavailable("Agent returned an invalid retried job")
        return body

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> Any:
        if not self._base_url:
            raise AgentRuntimeUnavailable("Agent runtime admin endpoint is not configured")
        try:
            response = await self._http.request(
                method,
                f"{self._base_url}{path}",
                params=params,
                timeout=httpx.Timeout(10.0, connect=2.0),
                headers={"Connection": "close"},
            )
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise AgentRuntimeUnavailable(
                f"Agent runtime request failed: {method} {path}: {exc}"
            ) from exc


__all__ = ["AgentRuntimeAdminClient", "AgentRuntimeUnavailable"]
