"""The loopback adapter to the process that holds the authority credentials.

A Protocol in the router and a concrete client here, so a test can inject an
in-process fake without a socket. What the boundary is *for* is credential
isolation, not testability (plan §3.4.1): the token this carries reaches one
loopback service, and the Data/Hub/Kernel credentials stay in that service.
"""

from __future__ import annotations

from urllib.parse import quote

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

    async def companion(self, *, owner_id: str, companion_id: str) -> dict:
        return await self._get(
            f"/api/internal/v1/management/companions/{quote(companion_id, safe='')}",
            {"owner_id": owner_id},
        )

    async def set_default_companion(
        self, *, owner_id: str, companion_id: str, expected_revision: int
    ) -> dict:
        return await self._put(
            "/api/internal/v1/management/owners/default-companion",
            {"owner_id": owner_id},
            {"companion_id": companion_id, "expected_revision": expected_revision},
        )

    async def create_companion(
        self,
        *,
        owner_id: str,
        operation_id: str,
        display_name: str,
        kind: str,
    ) -> dict:
        return await self._put(
            "/api/internal/v1/management/companion-provisions/"
            f"{quote(operation_id, safe='')}",
            {"owner_id": owner_id},
            {"display_name": display_name, "kind": kind},
        )

    async def memory_library(
        self, *, owner_id: str, companion_id: str | None
    ) -> dict:
        params = {"owner_id": owner_id}
        if companion_id:
            params["companion_id"] = companion_id
        return await self._get("/api/internal/v1/management/memory/library", params)

    async def forget_preview(
        self, *, owner_id: str, target: str, action: str
    ) -> dict:
        return await self._put(
            "/api/internal/v1/management/memory/forget/preview",
            {"owner_id": owner_id},
            {"target": target, "action": action},
            method="POST",
        )

    async def forget_confirm(self, *, owner_id: str, confirmation_token: str) -> dict:
        return await self._put(
            "/api/internal/v1/management/memory/forget/confirm",
            {"owner_id": owner_id},
            {"confirmation_token": confirmation_token},
            method="POST",
        )

    async def _get(self, path: str, params: dict[str, str]) -> dict:
        return await self._call("GET", path, params, None)

    async def _call(
        self, method: str, path: str, params: dict[str, str], body: dict | None
    ) -> dict:
        """One place that speaks to the other process, whatever the verb.

        Reads and writes differ only in method and body here — deliberately, so
        that "how a refusal is relayed" cannot come to mean two things.
        """
        if not self._service_token:
            raise ManagementBackendError(
                "Host management backend credential is not configured", status_code=503
            )
        try:
            response = await self._client.request(
                method,
                f"{self._base_url}{path}",
                params=params,
                json=body,
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
            # refusal from the other side means. A 409 in particular has to
            # arrive intact — it is the one a client must react to by re-reading
            # rather than by retrying.
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

    async def _put(
        self, path: str, params: dict[str, str], body: dict, *, method: str = "PUT"
    ) -> dict:
        return await self._call(method, path, params, body)

    async def close(self) -> None:
        await self._client.aclose()
