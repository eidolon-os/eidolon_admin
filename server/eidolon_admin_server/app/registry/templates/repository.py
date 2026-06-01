"""HTTP client to agent's template REST surface.

The "repository" role in this 4-layer module — but the backing store
lives in the agent project, not in admin. This file is the only place
that knows agent's URL shape and response wire format. If agent changes
its endpoints, only this file moves.

All methods return raw dicts (whatever agent sent back). Translation
to admin's wire shapes (``TemplateRef`` / ``TemplateDetail``) happens
in the orchestrator. This keeps the HTTP layer dumb and the schema
layer testable in isolation.

Errors:
  - Connection refused / timeout / DNS  → ``TemplateAgentUnreachable``
  - Agent returned HTTP 4xx/5xx         → ``TemplateUpstreamError`` with
                                          status code attached so the
                                          orchestrator can map cleanly.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class TemplateAgentUnreachable(Exception):
    """Network-level failure (refused, timeout, DNS). Admin should
    surface this as 503 — agent project is presumed down."""


class TemplateUpstreamError(Exception):
    """Agent responded with a 4xx/5xx. ``status_code`` carries the
    original code so the orchestrator can preserve it (e.g. 404 → 404
    rather than collapsing everything to 502)."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class TemplateAgentClient:
    """Thin wrapper over ``app.state.http_client`` for agent's template API.

    Uses the shared async client so connection pooling + timeouts come
    from one place. The ``base_url`` is the agent service's
    ``/api/admin`` root, looked up at lifespan time from services.yaml.
    """

    def __init__(self, http_client: httpx.AsyncClient, agent_base_url: str) -> None:
        self._http = http_client
        # Normalize: agent endpoints sit under /api/admin/personas/templates*
        # (the existing read endpoints) and /api/admin/personas/templates*
        # for the new write endpoints (added in agent 71b125b). The base
        # is the agent service's HTTP root.
        self._base = agent_base_url.rstrip("/")

    def _url(self, path: str) -> str:
        # path always starts with /api/admin/...
        return f"{self._base}{path}"

    async def list_templates(self) -> list[dict[str, Any]]:
        """GET /api/admin/personas/templates — list summaries (builtin+custom)."""
        try:
            r = await self._http.get(self._url("/api/admin/personas/templates"))
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise TemplateAgentUnreachable(str(exc)) from exc
        if r.status_code >= 400:
            raise TemplateUpstreamError(r.status_code, r.text)
        return r.json()

    async def get_template(self, template_id: str) -> dict[str, Any]:
        """GET /api/admin/personas/templates/{id} — full parsed template."""
        try:
            r = await self._http.get(
                self._url(f"/api/admin/personas/templates/{template_id}")
            )
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise TemplateAgentUnreachable(str(exc)) from exc
        if r.status_code == 404:
            raise TemplateUpstreamError(404, "template not found")
        if r.status_code >= 400:
            raise TemplateUpstreamError(r.status_code, r.text)
        return r.json()

    async def get_template_raw(self, template_id: str) -> str:
        """GET /api/admin/personas/templates/{id}/raw — original YAML text.

        Works for both builtin and custom (agent's existing endpoint
        merges across both sources).
        """
        try:
            r = await self._http.get(
                self._url(f"/api/admin/personas/templates/{template_id}/raw")
            )
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise TemplateAgentUnreachable(str(exc)) from exc
        if r.status_code == 404:
            raise TemplateUpstreamError(404, "template not found")
        if r.status_code >= 400:
            raise TemplateUpstreamError(r.status_code, r.text)
        return r.text

    async def create_custom(
        self,
        *,
        template_id: str,
        tenant_id: str,
        display_name: str,
        yaml_body: str,
        archetype: str = "custom",
    ) -> dict[str, Any]:
        body = {
            "template_id": template_id,
            "tenant_id": tenant_id,
            "display_name": display_name,
            "yaml_body": yaml_body,
            "archetype": archetype,
        }
        try:
            r = await self._http.post(
                self._url("/api/admin/personas/templates"), json=body,
            )
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise TemplateAgentUnreachable(str(exc)) from exc
        if r.status_code >= 400:
            raise TemplateUpstreamError(r.status_code, r.text)
        return r.json()

    async def update_custom(
        self,
        template_id: str,
        *,
        display_name: str | None = None,
        yaml_body: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if display_name is not None:
            body["display_name"] = display_name
        if yaml_body is not None:
            body["yaml_body"] = yaml_body
        try:
            r = await self._http.put(
                self._url(f"/api/admin/personas/templates/{template_id}"),
                json=body,
            )
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise TemplateAgentUnreachable(str(exc)) from exc
        if r.status_code >= 400:
            raise TemplateUpstreamError(r.status_code, r.text)
        return r.json()

    async def delete_custom(self, template_id: str) -> None:
        try:
            r = await self._http.delete(
                self._url(f"/api/admin/personas/templates/{template_id}")
            )
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise TemplateAgentUnreachable(str(exc)) from exc
        if r.status_code not in (200, 204):
            raise TemplateUpstreamError(r.status_code, r.text)

    async def fork(
        self,
        template_id: str,
        *,
        new_template_id: str,
        target_tenant_id: str,
        new_display_name: str,
    ) -> dict[str, Any]:
        try:
            r = await self._http.post(
                self._url(f"/api/admin/personas/templates/{template_id}/fork"),
                json={
                    "new_template_id": new_template_id,
                    "target_tenant_id": target_tenant_id,
                    "new_display_name": new_display_name,
                },
            )
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise TemplateAgentUnreachable(str(exc)) from exc
        if r.status_code >= 400:
            raise TemplateUpstreamError(r.status_code, r.text)
        return r.json()
