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


def _refusal_code(response: httpx.Response) -> str | None:
    """The authority's word for this refusal, if the other side gave one.

    Deliberately forgiving: any shape but the one it knows answers ``None``. A
    boundary that raised while relaying an error would replace a refusal a
    client could act on with a failure nobody can.
    """

    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    detail = payload.get("detail")
    if isinstance(detail, dict):
        code = detail.get("code")
        return str(code)[:64] if code else None
    return None


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

    async def set_companion_lifecycle(
        self,
        *,
        owner_id: str,
        companion_id: str,
        lifecycle_state: str,
        replacement_companion_id: str | None,
        expected_revision: int | None,
    ) -> dict:
        body: dict = {"lifecycle_state": lifecycle_state}
        if replacement_companion_id is not None:
            body["replacement_companion_id"] = replacement_companion_id
        if expected_revision is not None:
            body["expected_revision"] = expected_revision
        return await self._put(
            f"/api/internal/v1/management/companions/{quote(companion_id, safe='')}"
            "/lifecycle",
            {"owner_id": owner_id},
            body,
        )

    async def memory_library(
        self, *, owner_id: str, companion_id: str | None
    ) -> dict:
        params = {"owner_id": owner_id}
        if companion_id:
            params["companion_id"] = companion_id
        return await self._get("/api/internal/v1/management/memory/library", params)

    async def memory_entries(
        self,
        *,
        owner_id: str,
        since: str,
        limit: int | None,
        companion_id: str | None,
    ) -> dict:
        params = {"owner_id": owner_id, "since": since}
        if limit is not None:
            params["limit"] = str(limit)
        if companion_id:
            params["companion_id"] = companion_id
        return await self._get("/api/internal/v1/management/memory/entries", params)

    async def memory_export(
        self, *, owner_id: str, companion_id: str | None
    ) -> dict:
        params = {"owner_id": owner_id}
        if companion_id:
            params["companion_id"] = companion_id
        return await self._get("/api/internal/v1/management/memory/export", params)

    async def revoke_runtime_sessions(self, *, owner_id: str) -> dict:
        return await self._put(
            "/api/internal/v1/management/owner/runtime-session-revocations",
            {"owner_id": owner_id},
            {},
            method="POST",
        )

    async def conversations(
        self,
        *,
        owner_id: str,
        companion_id: str,
        limit: int | None,
        cursor: str | None,
    ) -> dict:
        params = {"owner_id": owner_id}
        if limit is not None:
            params["limit"] = str(limit)
        if cursor is not None:
            params["cursor"] = cursor
        return await self._get(
            "/api/internal/v1/management/companions/"
            f"{quote(companion_id, safe='')}/conversations",
            params,
        )

    async def transcript(
        self,
        *,
        owner_id: str,
        companion_id: str,
        conversation_id: str,
        limit: int | None,
        cursor: str | None,
    ) -> dict:
        params = {"owner_id": owner_id}
        if limit is not None:
            params["limit"] = str(limit)
        if cursor is not None:
            params["cursor"] = cursor
        return await self._get(
            "/api/internal/v1/management/companions/"
            f"{quote(companion_id, safe='')}/conversations/"
            f"{quote(conversation_id, safe='')}/turns",
            params,
        )

    async def tasks(
        self,
        *,
        owner_id: str,
        companion_id: str,
        limit: int | None,
        status: str | None,
        cursor: str | None,
    ) -> dict:
        params = {"owner_id": owner_id}
        if limit is not None:
            params["limit"] = str(limit)
        if status is not None:
            params["status"] = status
        if cursor is not None:
            params["cursor"] = cursor
        return await self._get(
            "/api/internal/v1/management/companions/"
            f"{quote(companion_id, safe='')}/tasks",
            params,
        )

    async def task(self, *, owner_id: str, companion_id: str, task_id: str) -> dict:
        return await self._get(
            "/api/internal/v1/management/companions/"
            f"{quote(companion_id, safe='')}/tasks/{quote(task_id, safe='')}",
            {"owner_id": owner_id},
        )

    async def task_action(
        self, *, owner_id: str, companion_id: str, task_id: str, action: str
    ) -> dict:
        # ``_put`` with an explicit method: the one helper that carries a body
        # also carries the verb, and a task action has no body to send.
        return await self._put(
            "/api/internal/v1/management/companions/"
            f"{quote(companion_id, safe='')}/tasks/{quote(task_id, safe='')}/{action}",
            {"owner_id": owner_id},
            {},
            method="POST",
        )

    async def persona_history(self, *, owner_id: str, companion_id: str) -> dict:
        return await self._get(
            "/api/internal/v1/management/companions/"
            f"{quote(companion_id, safe='')}/persona-history",
            {"owner_id": owner_id},
        )

    async def restore_persona(
        self, *, owner_id: str, companion_id: str, chapter_id: str
    ) -> dict:
        return await self._put(
            "/api/internal/v1/management/companions/"
            f"{quote(companion_id, safe='')}/persona-restorations",
            {"owner_id": owner_id},
            {"chapter_id": chapter_id},
        )

    async def recollections(
        self, *, owner_id: str, query: str, limit: int, companion_id: str | None
    ) -> dict:
        params = {"owner_id": owner_id, "q": query, "limit": str(limit)}
        if companion_id:
            params["companion_id"] = companion_id
        return await self._get(
            "/api/internal/v1/management/memory/recollections", params
        )

    async def assign_memory_audience(
        self, *, owner_id: str, entry_id: str, companion_id: str
    ) -> dict:
        return await self._put(
            "/api/internal/v1/management/memory/entries/"
            f"{quote(entry_id, safe='')}/audience",
            {"owner_id": owner_id},
            {"companion_id": companion_id},
        )

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
            #
            # The refusal code travels with it. Relaying the status alone was
            # enough while every refusal meant "look at this again", but not for
            # the ones that are a question — "who should answer instead?" — which
            # a client can only ask if it is told that is what happened.
            raise ManagementBackendError(
                "Host management backend refused this request",
                status_code=response.status_code
                if 400 <= response.status_code < 500
                else 503,
                code=_refusal_code(response),
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
