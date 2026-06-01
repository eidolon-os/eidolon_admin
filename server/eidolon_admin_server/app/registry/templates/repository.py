"""HTTP client to agent's template REST surface.

The "repository" role in this 4-layer module — but the backing store
lives in the agent project, not in admin. This file is the only place
that knows agent's URL shape and response wire format. If agent changes
its endpoints, only this file moves.

All methods return raw dicts (whatever agent sent back). Translation
to admin's wire shapes (``TemplateRef`` / ``TemplateDetail``) happens
in the orchestrator. This keeps the HTTP layer dumb and the schema
layer testable in isolation.

Errors are inherited from the shared ``SubProjectHTTPClient`` base
(``SubProjectUnreachable`` / ``SubProjectUpstreamError``); the module
re-exports them under template-specific names so existing imports
keep working.
"""
from __future__ import annotations

import logging
from typing import Any

from .._shared import (
    SubProjectHTTPClient,
    SubProjectUnreachable,
    SubProjectUpstreamError,
)

logger = logging.getLogger(__name__)


# Backwards-compatible aliases — preserve the old exception names so
# the orchestrator + tests keep importing what they always did.
TemplateAgentUnreachable = SubProjectUnreachable
TemplateUpstreamError = SubProjectUpstreamError


class TemplateAgentClient(SubProjectHTTPClient):
    """Thin client for agent's ``/api/admin/personas/templates*``."""

    async def list_templates(self) -> list[dict[str, Any]]:
        """GET /api/admin/personas/templates — list summaries (builtin+custom)."""
        r = await self._request("GET", "/api/admin/personas/templates")
        return r.json()

    async def get_template(self, template_id: str) -> dict[str, Any]:
        """GET /api/admin/personas/templates/{id} — full parsed template."""
        r = await self._request(
            "GET", f"/api/admin/personas/templates/{template_id}"
        )
        return r.json()

    async def get_template_raw(self, template_id: str) -> str:
        """GET /api/admin/personas/templates/{id}/raw — original YAML text.

        Works for both builtin and custom (agent's existing endpoint
        merges across both sources).
        """
        r = await self._request(
            "GET", f"/api/admin/personas/templates/{template_id}/raw"
        )
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
        r = await self._request(
            "POST",
            "/api/admin/personas/templates",
            json={
                "template_id": template_id,
                "tenant_id": tenant_id,
                "display_name": display_name,
                "yaml_body": yaml_body,
                "archetype": archetype,
            },
        )
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
        r = await self._request(
            "PUT", f"/api/admin/personas/templates/{template_id}", json=body
        )
        return r.json()

    async def delete_custom(self, template_id: str) -> None:
        # Agent returns 204 for successful delete; treat 200 as equally fine
        # for forwards-compat in case the contract relaxes.
        await self._request(
            "DELETE",
            f"/api/admin/personas/templates/{template_id}",
            ok_statuses=(200, 204),
        )

    async def fork(
        self,
        template_id: str,
        *,
        new_template_id: str,
        target_tenant_id: str,
        new_display_name: str,
    ) -> dict[str, Any]:
        r = await self._request(
            "POST",
            f"/api/admin/personas/templates/{template_id}/fork",
            json={
                "new_template_id": new_template_id,
                "target_tenant_id": target_tenant_id,
                "new_display_name": new_display_name,
            },
        )
        return r.json()
