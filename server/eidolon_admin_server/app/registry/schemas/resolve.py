"""Resolve — aggregate cross-entity lookups for runtime callers.

Channel (LiveKit voice) and the web client both need the same shape:
    "Given a device_id (or user_id), tell me everything I need to
     start a session — which agent, which user, which memory MCP url,
     what's the soul markdown."

Three sub-projects would have to be queried otherwise (hub for the
device, agent for the persona, memory for the MCP url). The
``/api/resolve/*`` endpoints do that join in admin and return one
flat response.

These are READ-ONLY. All authoring goes through the per-entity APIs.

Failure handling:
    Resolve is intolerant of partial state — if the device is
    unbound, or the bound agent is deleted, or memory is unreachable
    for the user, the endpoint returns 4xx/5xx with a precise reason.
    Callers should NOT silently fall back to a "default" anything;
    that's the silent-fallback anti-pattern we're explicitly removing.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class VoiceprintResolveSummary(BaseModel):
    enabled: bool = False
    profile_id: str | None = None
    provider: str | None = None
    model: str | None = None
    threshold: float | None = None


class ResolvedContext(BaseModel):
    """Everything a runtime session needs in one envelope.

    All fields are required when status="ok". On failure the endpoint
    returns an HTTP error code instead of a degraded ResolvedContext —
    we never want runtime code to receive a half-filled context.
    """

    tenant_id: str
    user_id: str
    agent_id: str
    template_id: str
    template_revision: int
    # The agent project's authoritative URL for talking to this agent
    # (e.g. for evolution events). Empty if not applicable.
    agent_runtime_url: str = ""
    # The memory project's MCP HTTP URL for this user. Channel uses this
    # to set up the MCP session for recall.
    memory_mcp_url: str
    # First 200 chars of the rendered soul — used for log tagging /
    # admin UI preview, NOT for the LLM (LLM gets the full soul from
    # the agent project).
    soul_preview: str
    # device_id is present only on the /resolve/device path. /resolve/user
    # paths omit it (the user might be talking via web client, no device).
    device_id: str | None = None
    # Phase 6: per-device interaction-mode override from the device binding.
    # Present only on the /resolve/device path when the operator set one; hub
    # gives it priority over the device's self-declared header. None = no
    # override.
    interaction_mode: str | None = None
    voiceprint: VoiceprintResolveSummary = Field(
        default_factory=VoiceprintResolveSummary
    )


class ResolveDeviceResponse(BaseModel):
    """GET /api/resolve/device/{device_id} response."""

    context: ResolvedContext


class ResolveUserResponse(BaseModel):
    """GET /api/resolve/user/{user_id} response.

    Resolves to the user's currently-active agent. If the user has no
    active agent (zero agents owned), the endpoint returns 412 "user
    has no agent configured" rather than picking arbitrarily.
    """

    context: ResolvedContext
