"""The loopback adapter to the process that holds the authority credentials.

A Protocol in the router and a concrete client here, so a test can inject an
in-process fake without a socket. What the boundary is *for* is credential
isolation, not testability (plan §3.4.1): the token this carries reaches one
loopback service, and the Data/Hub/Kernel credentials stay in that service.
"""

from __future__ import annotations

from urllib.parse import quote

import httpx
from pydantic import ValidationError

from eidolon_sdk.biz.contracts.refusal import Refusal

from eidolon_admin_server.local_api.management.router import (
    ManagementBackendError,
    refusal_for_status,
)


def _relayed_refusal(response: httpx.Response) -> Refusal:
    """The authority's own refusal, relayed rather than reinterpreted.

    This function is the fix for the hop that lost the answer. It used to read
    one field — ``code`` — and throw the rest away, so the sentence that
    identified the fault ("Admin memory service credential is not configured")
    was constructed by the authority, serialised, received *here*, and dropped,
    and every screen downstream said 被拒绝 with nothing after it.

    Deliberately forgiving about shape: a body this cannot parse becomes a
    refusal derived from the status rather than an exception. A boundary that
    raised while relaying an error would replace a refusal a client could act on
    with a failure nobody can.
    """

    try:
        payload = response.json()
    except ValueError:
        payload = None
    detail = payload.get("detail") if isinstance(payload, dict) else None
    if isinstance(detail, dict):
        if isinstance(detail.get("refusal"), dict):
            try:
                return Refusal.model_validate(detail["refusal"])
            except ValidationError:
                # A refusal we cannot parse is still a refusal. Falling through
                # loses the wording, never the fact.
                pass
        reason = detail.get("detail") or detail.get("message") or ""
        code = detail.get("code")
    else:
        reason = detail if isinstance(detail, str) else ""
        code = None
    return refusal_for_status(
        response.status_code,
        str(reason),
        str(code)[:64] if code else None,
    )


def _outside_contract() -> Refusal:
    """An answer this boundary cannot read is not the client's problem.

    ``upstream`` rather than ``invalid``: nothing about the request produced it
    and nothing about the request will fix it.
    """

    return Refusal(
        kind="upstream",
        reason="this Host answered outside its own contract",
        retryable=True,
    )


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
        persona: dict | None = None,
    ) -> dict:
        return await self._put(
            "/api/internal/v1/management/companion-provisions/"
            f"{quote(operation_id, safe='')}",
            {"owner_id": owner_id},
            {
                "display_name": display_name,
                "kind": kind,
                # Omitted rather than null when nobody authored anything, so the
                # request the authority fingerprints is byte-identical to what an
                # older client sends. That is what keeps a retry across an
                # upgrade a replay instead of a conflict.
                **({} if persona is None else {"persona": persona}),
            },
        )

    async def persona_authoring_template(self) -> dict:
        """What an Eidolon would be if the create form came back untouched."""

        return await self._get(
            "/api/internal/v1/management/persona-authoring-template", {}
        )

    async def activity(
        self, *, owner_id: str, limit: int | None, before: int | None
    ) -> dict:
        params = {"owner_id": owner_id}
        if limit is not None:
            params["limit"] = str(limit)
        if before is not None:
            params["before"] = str(before)
        return await self._get("/api/internal/v1/management/activity", params)

    async def companion_face_state(
        self, *, owner_id: str, companion_id: str
    ) -> dict:
        return await self._get(
            f"/api/internal/v1/management/companions/{quote(companion_id, safe='')}"
            "/face-state",
            {"owner_id": owner_id},
        )

    async def companion_face(
        self, *, owner_id: str, companion_id: str, known_etag: str | None
    ) -> tuple[int, bytes, str | None]:
        """The photograph, relayed as bytes and never decoded on the way.

        Answers with the upstream status rather than a parsed body: 200 with a
        face, 204 for an Eidolon that has none, 304 for the one the caller
        already holds. All three are things a screen does something different
        with, and flattening them into "here is a body or an error" would make
        the last one impossible to tell from the second.
        """

        response = await self._raw(
            "GET",
            f"/api/internal/v1/management/companions/{quote(companion_id, safe='')}"
            "/face",
            {"owner_id": owner_id},
            headers={"If-None-Match": known_etag} if known_etag else None,
        )
        if response.status_code not in {200, 204, 304}:
            refusal = _relayed_refusal(response)
            raise ManagementBackendError(
                refusal.reason or "Host management backend refused this request",
                status_code=response.status_code
                if 400 <= response.status_code < 500
                else 503,
                refusal=refusal,
            )
        return (
            response.status_code,
            response.content,
            response.headers.get("ETag"),
        )

    async def set_companion_face(
        self, *, owner_id: str, companion_id: str, face: bytes
    ) -> dict:
        response = await self._raw(
            "PUT",
            f"/api/internal/v1/management/companions/{quote(companion_id, safe='')}"
            "/face",
            {"owner_id": owner_id},
            headers={"Content-Type": "image/jpeg"},
            content=face,
        )
        return self._decoded(response)

    async def clear_companion_face(
        self, *, owner_id: str, companion_id: str
    ) -> dict:
        return await self._put(
            f"/api/internal/v1/management/companions/{quote(companion_id, safe='')}"
            "/face",
            {"owner_id": owner_id},
            {},
            method="DELETE",
        )

    async def rename_companion(
        self, *, owner_id: str, companion_id: str, display_name: str
    ) -> dict:
        return await self._put(
            f"/api/internal/v1/management/companions/{quote(companion_id, safe='')}",
            {"owner_id": owner_id},
            {"display_name": display_name},
            method="PATCH",
        )

    async def rename_owner(self, *, owner_id: str, display_name: str) -> dict:
        return await self._put(
            "/api/internal/v1/management/owner",
            {"owner_id": owner_id},
            {"display_name": display_name},
            method="PATCH",
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

    async def _raw(
        self,
        method: str,
        path: str,
        params: dict[str, str],
        *,
        body: dict | None = None,
        headers: dict[str, str] | None = None,
        content: bytes | None = None,
    ) -> httpx.Response:
        """One place that speaks to the other process, whatever the verb.

        Reads, writes and photographs differ only in method, body and content
        type here — deliberately, so that "how a refusal is relayed" cannot come
        to mean two things.
        """
        if not self._service_token:
            raise ManagementBackendError(
                "Host management backend credential is not configured",
                status_code=503,
                # Named as configuration, not as the backend being down. They are
                # the same status and different problems, and telling a person to
                # try again for one of them is telling them to wait forever.
                refusal=Refusal(
                    kind="not_configured",
                    reason="this Host is not configured to answer management requests",
                ),
            )
        try:
            return await self._client.request(
                method,
                f"{self._base_url}{path}",
                params=params,
                json=body,
                content=content,
                headers={
                    "Authorization": f"Bearer {self._service_token}",
                    **(headers or {}),
                },
                timeout=self._timeout,
            )
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
            raise ManagementBackendError(
                "Host management backend is unreachable",
                status_code=503,
                refusal=Refusal(
                    kind="not_running",
                    reason="this Host's management service is not answering",
                    retryable=True,
                ),
            ) from exc

    async def _call(
        self, method: str, path: str, params: dict[str, str], body: dict | None
    ) -> dict:
        return self._decoded(await self._raw(method, path, params, body=body))

    @staticmethod
    def _decoded(response: httpx.Response) -> dict:
        if response.status_code != 200:
            # Relayed rather than reinterpreted: this side has no credentials and
            # no authority facts, so it is in no position to decide what a
            # refusal from the other side means. A 409 in particular has to
            # arrive intact — it is the one a client must react to by re-reading
            # rather than by retrying.
            #
            # The whole refusal travels, not a field of it. This used to keep
            # ``code`` and substitute a sentence of its own for everything else,
            # which is how a Host that had been refusing every memory read for
            # two weeks told the phone exactly nothing: the authority's kind
            # ("not configured") and its sentence were both already here.
            refusal = _relayed_refusal(response)
            raise ManagementBackendError(
                refusal.reason or "Host management backend refused this request",
                status_code=response.status_code
                if 400 <= response.status_code < 500
                else 503,
                refusal=refusal,
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ManagementBackendError(
                "Host management backend answered outside its contract",
                status_code=502,
                refusal=_outside_contract(),
            ) from exc
        if not isinstance(payload, dict):
            raise ManagementBackendError(
                "Host management backend answered outside its contract",
                status_code=502,
                refusal=_outside_contract(),
            )
        return payload

    async def _put(
        self, path: str, params: dict[str, str], body: dict, *, method: str = "PUT"
    ) -> dict:
        return await self._call(method, path, params, body)

    async def close(self) -> None:
        await self._client.aclose()
