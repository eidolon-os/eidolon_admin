"""Repository layer for Agents — agent project HTTP + admin registry metadata.

Two stores composed in one module (same pattern as Users):

  - :class:`AgentProjectClient` — HTTP client for agent's
    ``/api/admin/personas/instances*`` (existing endpoints — created
    well before Phase 29). Sub-classes SDK
    :class:`ServiceHTTPClient` for shared transport.

  - :class:`AgentMetadataRepository` — SQLite-backed registry store.
    Stores the routing info admin needs
    to translate single ``agent_id`` to agent project's composite key
    ``(tenant_id, user_id, instance_id)``, plus the operator-chosen
    ``display_name``.

Why we DON'T just call agent's ``GET /personas/instances`` and scan for
the matching id: that endpoint returns ALL instances across users,
which scales linearly. Admin's metadata table gives us O(1) lookup
keyed by agent_id.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from eidolon_sdk.adapters.registry_sqlite import (
    AgentMetadataRepository as SqliteAgentMetadataRepository,
)
from eidolon_sdk.core.http import (
    ServiceHTTPClient,
)
from eidolon_sdk.biz.registry.models import AgentMetadataRecord

logger = logging.getLogger(__name__)


# ===== HTTP client to agent project =========================================


class AgentProjectClient(ServiceHTTPClient):
    """HTTP wrapper over agent's persona-instance endpoints.

    Agent's keys are composite (tenant, user, instance) — the
    orchestrator resolves admin's single ``agent_id`` to the composite
    by consulting admin's metadata KV BEFORE calling these methods.
    """

    async def list_instances(self) -> list[dict[str, Any]]:
        """GET /api/admin/personas/instances — global list across users."""
        r = await self._request("GET", "/api/admin/personas/instances")
        return r.json()

    async def get_instance(
        self, tenant_id: str, user_id: str, instance_id: str
    ) -> dict[str, Any]:
        """GET single instance by composite key."""
        path = f"/api/admin/personas/instances/{tenant_id}/{user_id}/{instance_id}"
        r = await self._request("GET", path)
        return r.json()

    async def create_instance(
        self,
        *,
        tenant_id: str,
        user_id: str,
        instance_id: str,
        template_id: str,
    ) -> dict[str, Any]:
        """POST /api/admin/personas/instances — renders template + persists."""
        r = await self._request(
            "POST",
            "/api/admin/personas/instances",
            json={
                "tenant_id": tenant_id,
                "user_id": user_id,
                "instance_id": instance_id,
                "template_id": template_id,
            },
        )
        return r.json()

    async def delete_instance(
        self, tenant_id: str, user_id: str, instance_id: str
    ) -> None:
        """DELETE removes the persona instance + audit rows. Agent returns
        ``{"deleted": instance_id}`` — admin doesn't need the body."""
        path = f"/api/admin/personas/instances/{tenant_id}/{user_id}/{instance_id}"
        await self._request("DELETE", path)

    async def get_evolution_history(
        self,
        tenant_id: str,
        user_id: str,
        instance_id: str,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        path = (
            f"/api/admin/personas/instances/{tenant_id}/{user_id}/"
            f"{instance_id}/evolution"
        )
        # The limit param goes on the URL; build manually to keep the
        # client base interface clean (it doesn't take params kwarg).
        r = await self._request("GET", f"{path}?limit={limit}")
        return r.json()

    async def render_template(self, template_id: str) -> dict[str, Any]:
        """POST /api/admin/personas/templates/{id}/render — returns
        ``{markdown, template_id, template_revision}``. Used by
        ``get_agent`` to surface a soul preview the operator can read."""
        r = await self._request(
            "POST",
            f"/api/admin/personas/templates/{template_id}/render",
        )
        return r.json()

    async def revoke_user_sessions(self, user_id: str) -> dict[str, Any]:
        """Phase 33.B1: ask agent to write ``revoked.user.<user_id>`` to
        the DEVICE_REVOCATIONS NATS KV bucket. ``PairingTokenVerifier``
        rejects every JWT carrying that user_id on next ``verify()``,
        cutting active LK sessions at their next chat() turn.

        Admin proxies via agent (vs writing the KV directly) to keep
        the ownership boundary: agent owns the bucket name + key
        convention. Admin just expresses intent ("revoke this user").

        Returns ``{user_id, revoked}`` on success;
        raises ``ServiceUnavailable`` / ``ServiceUpstreamError``.
        """
        from urllib.parse import quote

        r = await self._request(
            "POST",
            f"/api/admin/users/{quote(user_id, safe='')}/revoke-sessions",
        )
        return r.json()

    async def delete_user_data(self, user_id: str) -> dict[str, Any]:
        """Hard-delete agent-owned persistent data for one user."""
        from urllib.parse import quote

        r = await self._request(
            "DELETE",
            f"/api/admin/users/{quote(user_id, safe='')}/data",
            timeout=120.0,
        )
        return r.json()


# ===== admin's per-agent metadata registry =================================


@dataclass
class AgentMetadata:
    """Routing info admin owns about each agent.

    Stored fields are the bare minimum to translate admin's flat
    ``agent_id`` to agent project's composite ``(tenant, user, instance)``
    plus the operator-chosen label.
    """

    tenant_id: str
    user_id: str
    template_id: str
    template_revision: int
    display_name: str
    created_at: str  # ISO timestamp

    def to_json(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "template_id": self.template_id,
            "template_revision": self.template_revision,
            "display_name": self.display_name,
            "created_at": self.created_at,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "AgentMetadata":
        return cls(
            tenant_id=data.get("tenant_id", "default"),
            user_id=data["user_id"],
            template_id=data["template_id"],
            template_revision=int(data.get("template_revision", 1)),
            display_name=data.get("display_name", ""),
            created_at=data.get("created_at", ""),
        )


class AgentMetadataRepository:
    """SQLite-backed store for per-agent admin metadata."""

    def __init__(self, repository: SqliteAgentMetadataRepository) -> None:
        self._repo = repository

    async def get(self, agent_id: str) -> AgentMetadata | None:
        record = await self._repo.get(agent_id)
        if record is None:
            return None
        try:
            return _metadata_from_record(record)
        except Exception:
            logger.exception("agents: malformed registry entry %s", agent_id)
            return None

    async def put(self, agent_id: str, meta: AgentMetadata) -> None:
        await self._repo.put(_metadata_to_record(agent_id, meta))

    async def delete(self, agent_id: str) -> None:
        """Idempotent."""
        await self._repo.delete(agent_id)

    async def list_all(self) -> dict[str, AgentMetadata]:
        out: dict[str, AgentMetadata] = {}
        for record in (await self._repo.list_all()).values():
            try:
                meta = _metadata_from_record(record)
            except Exception:
                logger.exception(
                    "agents: malformed registry entry %s", record.agent_id
                )
                continue
            out[record.agent_id] = meta
        return out

    async def list_by_user(self, user_id: str) -> list[tuple[str, AgentMetadata]]:
        """Return (agent_id, metadata) pairs whose user_id matches.

        Used by the orchestrator's ``list_agents?user_id=`` filter.
        """
        out: list[tuple[str, AgentMetadata]] = []
        for agent_id, record in await self._repo.list_by_user(user_id):
            try:
                out.append((agent_id, _metadata_from_record(record)))
            except Exception:
                logger.exception(
                    "agents: malformed registry entry %s", agent_id
                )
        return out


def _metadata_from_record(record: AgentMetadataRecord) -> AgentMetadata:
    return AgentMetadata(
        tenant_id=record.tenant_id,
        user_id=record.user_id,
        template_id=record.template_id,
        template_revision=record.template_revision,
        display_name=record.display_name,
        created_at=record.created_at,
    )


def _metadata_to_record(agent_id: str, meta: AgentMetadata) -> AgentMetadataRecord:
    return AgentMetadataRecord(
        agent_id=agent_id,
        tenant_id=meta.tenant_id,
        user_id=meta.user_id,
        template_id=meta.template_id,
        template_revision=meta.template_revision,
        display_name=meta.display_name,
        created_at=meta.created_at,
    )
