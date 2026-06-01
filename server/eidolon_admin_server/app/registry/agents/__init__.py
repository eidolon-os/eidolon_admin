"""Agents — persona instances bound to a user, business logic in agent project.

Most complex of the 5 entity modules because admin orchestrates across
both sub-projects on every operation:

  - Create: validate user exists (memory) AND template exists (agent),
    then call agent to render+persist the instance.
  - Delete: cascade — clear any user's ``active_agent_id`` pointing at
    this agent, then call agent's delete.
  - Resolve a single ``agent_id`` to agent project's composite PK
    (tenant_id, user_id, instance_id) via admin's own metadata KV.

Layout mirrors the other entity modules (4-layer).
"""
from .orchestrator import (
    AgentBadRequest,
    AgentError,
    AgentNotFound,
    AgentOrchestrator,
    AgentProjectDown,
    AgentUserMismatch,
)
from .repository import (
    AgentMetadata,
    AgentMetadataRepository,
    AgentProjectClient,
)
from .router import router

__all__ = [
    "AgentBadRequest",
    "AgentError",
    "AgentMetadata",
    "AgentMetadataRepository",
    "AgentNotFound",
    "AgentOrchestrator",
    "AgentProjectClient",
    "AgentProjectDown",
    "AgentUserMismatch",
    "router",
]
