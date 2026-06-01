"""User — identity / data subject with memory palace.

Business implementation lives in eidolon_memory. Admin's role is:
    - Issue create / update / delete calls against memory's
      ``/api/admin/users`` REST surface (added in Phase 29.B).
    - Track tenant↔user mapping (admin-side, since memory has no tenant
      concept).
    - Expose a composed view for the UI: spec + memory health +
      currently-active agent reference (from agent project).

The orchestrator coordinates the cross-project parts. Memory itself
owns ``users.yaml``, palace directories, and the user-worker lifecycle
(it spawns a worker per user, responds to SIGHUP on yaml changes).
Admin DOES NOT touch ``users.yaml`` or palace files directly anymore.

Cascade:
    Deleting a user → memory deletes palace + terminates worker;
    admin separately deletes all agents owned by this user (via agent
    project's persona delete) and unbinds any device pointing at them.
"""
from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _validate_id(value: str, *, field_name: str) -> str:
    if not _ID_RE.match(value):
        raise ValueError(
            f"{field_name} must be 1-64 chars of [A-Za-z0-9_-]; got {value!r}"
        )
    return value


class ConsolidatorConfig(BaseModel):
    """Memory consolidator settings (per-user).

    Memory's consolidator runs in the user-worker process and periodically
    promotes drawer-level details into wing-level summaries. Exposing it
    here lets the operator tune it without editing yaml by hand.

    Defaults match memory's own DefaultConsolidatorConfig — keep in sync
    when memory changes them.
    """

    enabled: bool = True
    interval_hours: float = Field(6.0, gt=0)
    window_days: int = Field(30, gt=0)
    min_drawers: int = Field(3, ge=1)
    min_confidence: float = Field(0.6, ge=0.0, le=1.0)


class UserSpec(BaseModel):
    """The persisted contract for a user.

    Stored in memory's users.yaml (memory writes); admin's tenant↔user
    mapping is layered on top via the orchestrator. ``palace_path`` is
    optional — empty string means "let memory pick the default location
    under ~/eidolon/memory/mempalaces/<user_id>".
    """

    user_id: str = Field(..., min_length=1, max_length=64)
    tenant_id: str = Field("default", min_length=1, max_length=64)
    display_name: str = Field(..., min_length=1, max_length=128)
    palace_path: str = ""  # empty = use memory's default
    consolidator: ConsolidatorConfig = Field(default_factory=ConsolidatorConfig)
    created_at: datetime

    @field_validator("user_id")
    @classmethod
    def _check_user_id(cls, v: str) -> str:
        return _validate_id(v, field_name="user_id")

    @field_validator("tenant_id")
    @classmethod
    def _check_tenant_id(cls, v: str) -> str:
        return _validate_id(v, field_name="tenant_id")


class UserHealth(BaseModel):
    """Runtime liveness of the user's memory backend.

    Source: admin probes memory project's ``/api/admin/users/{id}``
    endpoint, which in turn checks: is the worker process up? does its
    MCP /mcp accept a session? is the palace SQLite intact?
    """

    worker_running: bool
    mcp_reachable: bool
    palace_initialized: bool
    # Free-form short message for the UI ("worker booting, retry in 5s",
    # "palace corrupted, restore from snapshot", etc.). Empty when healthy.
    note: str = ""


class UserView(BaseModel):
    """Composed view returned by GET /api/users/{id}.

    Combines:
      - ``spec``: the persistent record (from memory + admin's tenant tag)
      - ``health``: liveness probe (admin → memory)
      - ``active_agent_id``: which agent web-client should use by default
        for this user (admin's bookkeeping; can be None if the user has
        zero agents)
      - ``agent_ids``: all agents owned by this user (from agent project)
      - ``mcp_http_url``: the user-worker's MCP endpoint (memory is
        authoritative for the port; admin propagates it so channel can
        dial straight from /api/resolve without a second round-trip).
        Empty string when memory is unreachable.
    """

    spec: UserSpec
    health: UserHealth
    active_agent_id: str | None = None
    agent_ids: list[str] = Field(default_factory=list)
    mcp_http_url: str = ""


class CreateUserRequest(BaseModel):
    user_id: str = Field(..., min_length=1, max_length=64)
    tenant_id: str = Field("default", min_length=1, max_length=64)
    display_name: str = Field(..., min_length=1, max_length=128)
    palace_path: str = ""
    consolidator: ConsolidatorConfig = Field(default_factory=ConsolidatorConfig)

    @field_validator("user_id")
    @classmethod
    def _check_user_id(cls, v: str) -> str:
        return _validate_id(v, field_name="user_id")

    @field_validator("tenant_id")
    @classmethod
    def _check_tenant_id(cls, v: str) -> str:
        return _validate_id(v, field_name="tenant_id")


class UpdateUserRequest(BaseModel):
    """PUT /api/users/{id}. user_id and tenant_id are immutable (PK + scope)."""

    display_name: str | None = Field(None, min_length=1, max_length=128)
    consolidator: ConsolidatorConfig | None = None


class SetActiveAgentRequest(BaseModel):
    """POST /api/users/{id}/set-active-agent body."""

    agent_id: str = Field(..., min_length=1)


class UserListResponse(BaseModel):
    users: list[UserView]
    # If memory was unreachable when this was built, the views will have
    # mcp_reachable=False but the spec list is still authoritative (admin
    # has its own tenant↔user mapping).
    memory_available: bool
