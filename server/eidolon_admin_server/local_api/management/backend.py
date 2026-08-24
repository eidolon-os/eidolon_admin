"""The loopback adapter to the process that holds the authority credentials.

A Protocol in the router and a concrete client here, so a test can inject an
in-process fake without a socket. What the boundary is *for* is credential
isolation, not testability (plan §3.4.1): the token this carries reaches one
loopback service, and the Data/Hub/Kernel credentials stay in that service.
"""

from __future__ import annotations

import httpx

from eidolon_admin_server.local_api.management.router import ManagementBackendError


class AdminManagementClient:
    """Calls Admin's internal management ABI over loopback."""

    def __init__(
        self,
        *,
        base_url: str,
        service_token: str,
        client: httpx.AsyncClient,
        timeout_seconds: float,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._service_token = service_token
        self._client = client
        self._timeout = timeout_seconds

    async def context(self, *, owner_id: str) -> dict:
        return await self._get("/api/internal/v1/management/context", {"owner_id": owner_id})

    async def roster(self, *, owner_id: str, cursor: str | None) -> dict:
        params = {"owner_id": owner_id}
        if cursor is not None:
            # Passed through untouched in both directions. This side never reads
            # a cursor: the page boundary belongs to the authority that built it.
            params["cursor"] = cursor
        return await self._get("/api/internal/v1/management/companions", params)

    async def _get(self, path: str, params: dict[str, str]) -> dict:
        if not self._service_token:
            raise ManagementBackendError(
                "Host management backend credential is not configured", status_code=503
            )
        try:
            response = await self._client.get(
                f"{self._base_url}{path}",
                params=params,
                headers={"Authorization": f"Bearer {self._service_token}"},
                timeout=self._timeout,
            )
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
            raise ManagementBackendError(
                "Host management backend is unreachable", status_code=503
            ) from exc
        if response.status_code != 200:
            # Relayed rather than reinterpreted: this side has no credentials and
            # no authority facts, so it is in no position to decide what a
            # refusal from the other side means.
            raise ManagementBackendError(
                "Host management backend refused this request",
                status_code=response.status_code
                if 400 <= response.status_code < 500
                else 503,
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ManagementBackendError(
                "Host management backend answered outside its contract", status_code=502
            ) from exc
        if not isinstance(payload, dict):
            raise ManagementBackendError(
                "Host management backend answered outside its contract", status_code=502
            )
        return payload

    async def close(self) -> None:
        await self._client.aclose()
