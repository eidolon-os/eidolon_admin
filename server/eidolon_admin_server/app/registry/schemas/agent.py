"""Agent — persona instance attached to a user.

Business implementation lives in eidolon_agent (the persona instance,
soul rendering, knob overlays, evolution). Admin's role:
    - Issue create / update / delete against agent project's
      ``/api/admin/personas`` REST surface.
    - Track per-user ``active_agent_id`` (admin-side bookkeeping; one
      user can own N agents, only one is the "default" for incoming
      sessions that don't specify an agent_id).
    - Track the agent↔user ownership invariant: any agent has exactly
      one user; any user can have many agents.

Soul:
    Rendered once at creation time from the template's current revision.
    Stored by the agent project (its NATS KV souls bucket or similar
    — admin treats it as opaque, retrieved via REST). The operator can
    later override the soul with manual edits without re-rendering;
    the override stays until they explicitly ``regenerate``.

Knob overlays:
    Real-time tuning of a specific agent's behavioral knobs without
    forking the template. E.g. "make this caretaker_jiezhi more formal
    today" sets ``{"warmth": 0.4}`` without touching the template.

Evolution state:
    The agent's accumulated drift over time per the template's
    evolution_rules. Read-only here (modified by agent project as a
    side effect of conversations); admin just surfaces it for the UI.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, RootModel


# A knob overlay is a flat map of knob name → float value.
# Bounded element type via Annotated keeps the JSON shape small + validated.
class KnobOverlay(RootModel[dict[str, Annotated[float, Field(ge=0.0, le=1.0)]]]):
    """Overlay map: knob name → value in [0, 1].

    The agent project clamps these to the template's declared
    [min, max] range at render time; we accept any value in [0, 1]
    and let agent do the per-knob clamp.
    """


class AgentRef(BaseModel):
    """Lightweight summary for list views."""

    agent_id: str
    user_id: str
    template_id: str
    template_revision: int
    display_name: str  # operator-set, defaults to "<user>'s <template>"
    created_at: datetime
    updated_at: datetime
    # True if this agent is the user's active default (i.e. picked when an
    # incoming session specifies user_id but no agent_id).
    is_active_for_user: bool


class AgentDetail(BaseModel):
    """Full detail returned by GET /api/agents/{id}."""

    ref: AgentRef
    soul_md: str
    soul_size_bytes: int
    knob_overlays: KnobOverlay
    # Evolution snapshot: per-knob current value, last-changed timestamp,
    # recent events. Free-form JSON because the schema is agent's domain;
    # we just pipe it through. Use Any-typed dict to stay schema-flexible.
    evolution_state: dict = Field(default_factory=dict)
    # The template the agent was rendered from is referenced by id + rev
    # in ``ref``. UI fetches the template detail separately if needed.


class CreateAgentRequest(BaseModel):
    """POST /api/agents — create a new persona instance."""

    user_id: str = Field(..., min_length=1, max_length=64)
    template_id: str = Field(..., min_length=1, max_length=64)
    # Optional human-friendly name; if absent the orchestrator names it
    # "<user_id>'s <template_id>".
    display_name: str | None = Field(None, min_length=1, max_length=128)
    # If True (default), the newly-created agent becomes the user's active
    # default. The previous active agent is demoted but not deleted.
    set_active: bool = True


class UpdateAgentKnobsRequest(BaseModel):
    """PUT /api/agents/{id}/knobs body — replace the overlay map."""

    knob_overlays: KnobOverlay


class UpdateAgentSoulRequest(BaseModel):
    """PUT /api/agents/{id}/soul body — operator hand-edit of rendered soul.

    This overrides the template-rendered soul without changing the
    template. To revert, POST /api/agents/{id}/soul/regenerate.
    """

    markdown: str = Field(..., min_length=1)


class AgentListResponse(BaseModel):
    agents: list[AgentRef]
    # True if the agent project responded. False → empty list, admin
    # banners "agent service unavailable".
    upstream_available: bool
