"""Pydantic models for the /api/devices surface.

These are the *wire* shapes the admin gateway speaks. They are intentionally
different from (a) hub's ``AdminDevice`` schema and (b) agent's
``PersonaTemplate`` — admin orchestrates across both worlds and exposes its
own composite view rather than leaking the upstream shapes.

Why separate from the repository's internal payload format:
    The repository layer reads / writes raw dicts into NATS JSON bytes. The
    router speaks Pydantic. Keeping these distinct means we can evolve the
    NATS payload (e.g. add a field) without touching the public API, and
    vice versa.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


# ---- request bodies --------------------------------------------------------


class CreateAgentRequest(BaseModel):
    """POST /api/devices/{device_id}/agents body."""

    template_id: str
    user_id: str = Field(..., min_length=1)


class SwitchActiveAgentRequest(BaseModel):
    """POST /api/devices/{device_id}/active-agent body."""

    agent_id: str = Field(..., min_length=1)


class UpdateSoulRequest(BaseModel):
    """PUT /api/devices/{device_id}/agents/{agent_id}/soul body."""

    markdown: str


# ---- response payloads (composed views) ------------------------------------


class AgentEntry(BaseModel):
    """One agent's row inside a device's binding view.

    Combines NATS ``agents`` bucket metadata with a derived ``is_active``
    flag (true if this agent_id == the mapping's active_agent_id). The
    frontend renders the active flag as a star / highlight.
    """

    agent_id: str
    template_id: str
    template_revision: int
    owner_user_id: str
    owner_device_id: str
    created_at: datetime
    updated_at: datetime
    is_active: bool


class DeviceBindingView(BaseModel):
    """The NATS-side binding info, when it exists.

    Distinguished from ``None`` (no binding row in mappings at all) so the
    frontend can tell "approved but no agents yet" from "has agents".
    """

    user_id: str
    agent_ids: list[str]
    active_agent_id: str | None
    updated_at: datetime
    agents: list[AgentEntry]


class DeviceView(BaseModel):
    """The composed device row admin's UI consumes.

    Sources:
    - ``device_id``, ``name``, ``approved``, ``paired``, ``last_seen``,
      ``status``: from hub /api/admin/devices
    - ``binding``: from NATS mappings + agents buckets (None if no row)
    """

    device_id: str
    name: str
    approved: bool
    approved_at: datetime | None
    paired: bool
    enabled: bool
    last_seen: datetime | None
    status: str  # "online" / "offline" / "degraded" / "unknown" from hub
    binding: DeviceBindingView | None


class DeviceListResponse(BaseModel):
    devices: list[DeviceView]
    # Whether NATS was reachable. False → binding fields will all be None
    # and admin should show a banner. The frontend cares because absence of
    # ``binding`` should mean "no agents" only when NATS is up.
    nats_available: bool


# ---- operation responses ---------------------------------------------------


class ApproveResponse(BaseModel):
    device_id: str
    approved: bool
    approved_at: datetime | None


class CreateAgentResponse(BaseModel):
    agent_id: str
    soul_preview_chars: int
    is_active: bool


class SwitchActiveResponse(BaseModel):
    device_id: str
    active_agent_id: str | None


class DeleteAgentResponse(BaseModel):
    device_id: str
    deleted_agent_id: str
    new_active_agent_id: str | None
    fallback_kind: Literal["next_newest", "cleared", "no_change"]


class SoulResponse(BaseModel):
    agent_id: str
    markdown: str
    size_bytes: int


class UpdateSoulResponse(BaseModel):
    agent_id: str
    size_bytes: int
