"""Users — identity layer, admin registry source of truth.

Admin owns the project-wide user catalog in SQLite:

  - admin owns user existence, enabled state, tenant scope, memory port,
    consolidator config, and ``active_agent_id``.
  - memory consumes the admin registry and executes runtime state:
    user-worker lifecycle, palace data, and health probes.
  - admin's REST surface (``/api/users``) returns registry rows enriched
    with best-effort memory health.

The files mirror Tenants/Templates:

    repository.py       HTTP client to memory health/reconcile endpoints
                        plus admin-local helper wiring.

    orchestrator.py     CRUD + cross-project orchestration. Writes SQLite
                        first, then asks memory to reconcile.

    router.py           HTTP I/O. Same status-code mapping pattern as
                        templates.

Schemas live one level up at ``..schemas.user`` (locked in 29.A).
"""
from .orchestrator import (
    TenantNotFoundForUser,
    UserAlreadyExists,
    UserError,
    UserLifecycleVerifyFailed,
    UserMemoryDown,
    UserNotFound,
    UserOrchestrator,
)
from .repository import MemoryUserClient
from .router import router

__all__ = [
    "MemoryUserClient",
    "TenantNotFoundForUser",
    "UserAlreadyExists",
    "UserError",
    "UserLifecycleVerifyFailed",
    "UserMemoryDown",
    "UserNotFound",
    "UserOrchestrator",
    "router",
]
