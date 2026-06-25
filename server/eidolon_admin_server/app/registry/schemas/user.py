"""User — identity / data subject with memory palace.

Admin owns the user registry in local SQLite: existence, tenant scope,
enabled state, memory port, consolidator config, and active agent. Memory
consumes that registry and owns runtime execution: user-worker lifecycle,
palace data, and health probes.

The API exposes a composed view for the UI: registry spec + memory health +
currently-active agent reference.

Cascade:
    Deleting a user → memory deletes palace + terminates worker;
    admin separately deletes all agents owned by this user (via agent
    project's persona delete) and unbinds any device pointing at them.
"""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from eidolon_sdk.biz.registry.ids import validate_registry_id
from eidolon_sdk.biz.registry.models import ConsolidatorConfig, UserSpec


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
      - ``spec``: the persistent record from admin's registry
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
    enabled: bool = False
    palace_path: str = ""
    consolidator: ConsolidatorConfig = Field(default_factory=ConsolidatorConfig)

    @field_validator("user_id")
    @classmethod
    def _check_user_id(cls, v: str) -> str:
        return validate_registry_id(v, field_name="user_id")

    @field_validator("tenant_id")
    @classmethod
    def _check_tenant_id(cls, v: str) -> str:
        return validate_registry_id(v, field_name="tenant_id")


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
