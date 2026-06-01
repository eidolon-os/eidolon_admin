"""Resolve orchestrator — joins five entities into one runtime envelope.

Sources consulted on each call:

  - admin's DeviceBindingRepository (KV) — device → agent_id
  - admin's AgentMetadataRepository (KV) — agent → (user, template, ...)
  - admin's UserOrchestrator → memory's GET /api/admin/users/{id}
    → memory MCP URL
  - admin's TemplateOrchestrator → agent's GET /api/admin/personas/templates/{id}/raw
    → soul preview text

No own storage — purely an aggregator. Stateless apart from the
orchestrator references it composes.
"""
from __future__ import annotations

import logging
from typing import Any

from ..agents.repository import AgentMetadataRepository
from ..devices.repository import DeviceBindingRepository
from ..schemas.resolve import ResolvedContext
from ..templates.orchestrator import TemplateOrchestrator
from ..users.orchestrator import UserError, UserNotFound, UserOrchestrator

logger = logging.getLogger(__name__)


# ---- exceptions ------------------------------------------------------------


class ResolveError(Exception):
    """Base — router maps via ``status_code``."""

    status_code: int = 500


class ResolveDeviceNotBound(ResolveError):
    """Device exists in hub but has no binding row in admin's KV.
    Returns 412 — caller must POST to /api/devices/{id}/bind first."""

    status_code = 412


class ResolveUserNoActiveAgent(ResolveError):
    """User exists in memory but admin's metadata has no
    active_agent_id set. Returns 412 — operator must
    POST /api/users/{id}/set-active-agent first."""

    status_code = 412


class ResolveError404(ResolveError):
    """Device or user doesn't exist."""

    status_code = 404


class ResolveUpstreamDown(ResolveError):
    """Memory, hub, or agent unreachable while resolving."""

    status_code = 503


# ---- orchestrator ----------------------------------------------------------


# Length of the soul preview baked into the context. Just enough for
# log tagging / admin UI hint; the LLM gets the full soul via agent
# project's render endpoint on the hot path.
_SOUL_PREVIEW_CHARS = 200


class ResolveOrchestrator:
    def __init__(
        self,
        *,
        binding_repo: DeviceBindingRepository,
        agent_meta_repo: AgentMetadataRepository,
        user_orchestrator: UserOrchestrator,
        template_orchestrator: TemplateOrchestrator,
    ) -> None:
        self._bindings = binding_repo
        self._agents = agent_meta_repo
        self._users = user_orchestrator
        self._templates = template_orchestrator

    # ---- private helpers ----------------------------------------------

    async def _compose_context(
        self,
        *,
        agent_id: str,
        device_id: str | None,
    ) -> ResolvedContext:
        """Given an agent_id (and optionally a device_id), assemble the
        runtime context. Raises ResolveError404/ResolveUpstreamDown as
        the underlying lookups fail."""

        # Agent metadata: tenant, user, template
        agent_meta = await self._agents.get(agent_id)
        if agent_meta is None:
            raise ResolveError404(
                f"agent {agent_id!r} not found in admin registry "
                "(device/user may reference a deleted agent)"
            )

        # User → memory MCP URL.
        try:
            user_view = await self._users.get_user(agent_meta.user_id)
        except UserNotFound as exc:
            raise ResolveError404(
                f"agent {agent_id!r} references user "
                f"{agent_meta.user_id!r} which doesn't exist in memory"
            ) from exc
        except UserError as exc:
            # Phase 29.H: narrowed from bare ``Exception`` — only catch
            # admin's own UserError subclasses (UserMemoryDown etc.) and
            # let programming bugs surface. UserMemoryDown is the
            # expected 503 path.
            raise ResolveUpstreamDown(
                f"memory unreachable resolving user "
                f"{agent_meta.user_id!r}: {exc}"
            ) from exc

        # Memory MCP url: memory uses port-per-user; admin doesn't
        # remember the port directly — we'd need an additional memory
        # lookup. For 29.G we expose the URL through a known pattern
        # derived from the user record. memory's MemoryUserClient.get_user
        # already returns the spec which is what we need, but doesn't
        # carry the MCP URL explicitly — that's a known gap. For now
        # we synthesize from the env. Fix in 29.K cleanup.
        memory_mcp_url = _build_memory_mcp_url(agent_meta.user_id)

        # Soul preview — small, only for log tagging
        try:
            raw_yaml = await self._templates._client.get_template_raw(
                agent_meta.template_id
            )
            soul_preview = raw_yaml[:_SOUL_PREVIEW_CHARS]
        except Exception as exc:
            # Soul preview is non-fatal — log + leave empty so resolve
            # still works in the rare case agent project's template
            # endpoint blips.
            logger.warning(
                "resolve: soul preview lookup failed for template %s: %s",
                agent_meta.template_id, exc,
            )
            soul_preview = ""

        return ResolvedContext(
            tenant_id=agent_meta.tenant_id,
            user_id=agent_meta.user_id,
            agent_id=agent_id,
            template_id=agent_meta.template_id,
            template_revision=agent_meta.template_revision,
            agent_runtime_url="",  # not yet exposed
            memory_mcp_url=memory_mcp_url,
            soul_preview=soul_preview,
            device_id=device_id,
        )

    # ---- public API ----------------------------------------------------

    async def resolve_device(self, device_id: str) -> ResolvedContext:
        """Resolve a device through to its full runtime context.

        Failure modes:
          - binding missing → 412 "device not configured"
          - binding present but agent gone (drift) → 404 with diagnostic
          - underlying lookups fail (memory/agent down) → 503
        """
        binding = await self._bindings.get(device_id)
        if binding is None:
            raise ResolveDeviceNotBound(
                f"device {device_id!r} is not bound to an agent. "
                "POST /api/devices/{id}/bind to configure."
            )
        return await self._compose_context(
            agent_id=binding.agent_id, device_id=device_id,
        )

    async def resolve_user(self, user_id: str) -> ResolvedContext:
        """Resolve a user through their active_agent_id.

        Failure modes:
          - user has no admin metadata → 404
          - admin metadata has no active_agent_id → 412
          - underlying lookups fail → 503
        """
        # We use list_all to avoid a second roundtrip for active_agent_id
        # — get_user already calls memory; using the metadata repo
        # directly here is one KV read.
        meta = await self._users._meta.get(user_id)  # type: ignore[attr-defined]
        if meta is None:
            raise ResolveError404(
                f"user {user_id!r} not registered with admin "
                "(does memory know about them but admin doesn't? — "
                "create via POST /api/users to claim)"
            )
        if not meta.active_agent_id:
            raise ResolveUserNoActiveAgent(
                f"user {user_id!r} has no active agent. POST "
                f"/api/users/{user_id}/set-active-agent to configure."
            )
        return await self._compose_context(
            agent_id=meta.active_agent_id, device_id=None,
        )


def _build_memory_mcp_url(user_id: str) -> str:
    """Build the memory MCP URL for ``user_id``.

    Memory's MCP port assignment is per-user; admin doesn't currently
    cache the port in its own KV (user metadata bucket is light). For
    the dev stack the user-id → port mapping comes from memory's
    discovery endpoint. As a near-term placeholder we synthesize from
    the convention used by ``MemoryUserAdmin``:

      port 8030 = "default", subsequent users get 8031, 8032, ...

    For Phase 29.G this is good enough because the only caller (channel)
    will issue this URL straight to memory's MCP layer and memory will
    refuse if there's no listener. 29.K should replace this with a real
    lookup against memory's GET /api/admin/users/{id} (which carries
    the port in spec.consolidator? no — port is missing from memory's
    view envelope; need to add it).

    Marked here so the cleanup phase has a clear pointer.
    """
    # TODO(29.K): real lookup. For now: synth.
    import os

    # The discovery service exposes the MCP URLs at
    # /api/discovery/agent-routing — but admin already has the user
    # metadata; we just need user→port. The simplest stub for now is
    # to read EIDOLON_MEMORY_MCP_PORT for the default user and assume
    # consecutive ports for other users.
    base_port = int(os.environ.get("EIDOLON_MEMORY_MCP_PORT", "8030"))
    # We don't know the user's port without another lookup. Return the
    # base port — channel will likely fail for non-default users until
    # 29.K wires the real lookup. This is a deliberate caveat documented
    # in the design doc.
    return f"http://127.0.0.1:{base_port}/mcp"
