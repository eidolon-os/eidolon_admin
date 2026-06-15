"""Users — identity layer, business logic in memory project.

Admin's role mirrors the Templates module's relationship to agent:

  - memory owns palace + user-worker + ``users.yaml`` (business
    implementation)
  - admin owns the tenant↔user mapping and the per-user
    ``active_agent_id`` (cross-cutting bookkeeping memory has no
    notion of)
  - admin's REST surface (``/api/users``) composes both

The four files mirror Tenants/Templates:

    repository.py       HTTP client to memory's /api/admin/users/*
                        AND SQLite adapter for admin's per-user metadata.
                        Two stores, one repository module —
                        because admin always reads BOTH sources to build
                        a complete UserView.

    orchestrator.py     CRUD + cross-project orchestration. Atomicity:
                        a memory create followed by a SQLite metadata write
                        is two operations; the orchestrator compensates
                        if step 2 fails (deletes the memory user that
                        step 1 created).

    router.py           HTTP I/O. Same status-code mapping pattern as
                        templates.

Schemas live one level up at ``..schemas.user`` (locked in 29.A).
"""
from .orchestrator import (
    TenantNotFoundForUser,
    UserAlreadyExists,
    UserError,
    UserMemoryDown,
    UserNotFound,
    UserOrchestrator,
)
from .repository import (
    MemoryUserClient,
    MemoryUserUnreachable,
    MemoryUserUpstreamError,
    UserMetadataRepository,
)
from .router import router

__all__ = [
    "MemoryUserClient",
    "MemoryUserUnreachable",
    "MemoryUserUpstreamError",
    "TenantNotFoundForUser",
    "UserAlreadyExists",
    "UserError",
    "UserMemoryDown",
    "UserMetadataRepository",
    "UserNotFound",
    "UserOrchestrator",
    "router",
]
