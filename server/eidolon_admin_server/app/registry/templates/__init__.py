"""Templates — operator-authored persona templates, business logic in agent project.

This is admin's *proxy + composition* layer. The schema, the renderer,
the actual storage all live in the eidolon_agent project; admin's job
is to:

  - expose a stable REST surface (``/api/templates``) the UI/CLI can use
  - translate to agent's ``/api/admin/personas/templates*`` endpoints
  - graceful 5xx when agent is unreachable (no silent fallback)

Layout mirrors ``tenants/`` (4-layer module). The only difference is
that ``repository.py`` is an HTTP client to agent rather than a NATS
KV adapter — same role (CRUD primitive layer), different transport.
"""
from .orchestrator import (
    TemplateError,
    TemplateNotFound,
    TemplateOrchestrator,
)
from .repository import TemplateAgentClient
from .router import router

__all__ = [
    "TemplateAgentClient",
    "TemplateError",
    "TemplateNotFound",
    "TemplateOrchestrator",
    "router",
]
