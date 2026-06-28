"""Device — physical access point that runs an agent.

Two halves:
    - Device fact (id, kind, approved, last_seen): lives in eidolon_hub.
      Admin reads via hub's ``/api/admin/devices`` REST surface and
      issues approve/unregister/pairing-code through hub.
    - Device↔Agent binding (which agent runs on this device): an
      editorial decision the operator makes in admin UI, so admin
      stores it in the shared registry SQLite DB.

The binding is intentionally **separate from hub**:
    - hub has no agent concept; it knows about devices and pair codes.
    - The same device might rebind to different agents over its lifetime
      (e.g. operator switches the persona on an ESP32). Storing in admin
      lets us keep this clean of hub's domain.

This replaces the device-centric agent-creation pattern from Phase 25
(``POST /api/devices/{id}/agents``): in the new model, agents are
created independently under a user, and devices are bound to existing
agents via ``POST /api/devices/{id}/bind {agent_id}``.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

DeviceKind = Literal["web", "esp32", "mobile", "unknown"]

# Phase 6: per-device interaction-mode override. When set, it takes priority
# over the device's self-declared X-Device-Interaction-Mode header at hub
# resolve time (operator override > device self-declaration). None = no
# override, fall back to the device-declared / defaulted mode.
InteractionMode = Literal["half_duplex", "full_duplex"]


class DeviceBinding(BaseModel):
    """Admin-owned pointer from device to agent.

    Stored as one row in the ``device_bindings`` registry table.
    A device with no binding row is "approved but not configured" —
    voice/chat sessions for it must return HTTP 412 until bound.
    """

    agent_id: str = Field(..., min_length=1)
    bound_at: datetime
    # Phase 6: optional operator override of the device's interaction mode.
    interaction_mode: InteractionMode | None = None


class DeviceView(BaseModel):
    """Composed view shown in admin UI.

    Sources:
        - persistent device fact (id, kind, name, approved, enabled, last_seen):
          hub /api/admin/devices
        - runtime reachability (status, room, participant, missed probes):
          hub's LiveKit presence probe
        - binding (agent_id + bound_at): Eidolon Data grant metadata (None if not
          bound yet)
        - resolved_user_id / resolved_template_id (optional):
          orchestrator joins binding → agent → user. UI shows these
          inline so the operator doesn't have to drill through agent
          detail to see "what does this device speak as".
    """

    device_id: str
    name: str
    kind: DeviceKind = "unknown"
    enabled: bool = True
    approved: bool
    approved_at: datetime | None
    last_seen: datetime | None
    # Last HTTP client IP observed by Hub when the device requested /api/config.
    # This proves Hub reachability only; LiveKit reachability is status/room/sid.
    last_ip: str = ""
    # Hub LiveKit presence overlay ("online" / "offline" / "degraded" / "unknown").
    status: str
    room_name: str = ""
    participant_sid: str = ""
    missed_probes: int = 0
    binding: DeviceBinding | None = None
    # Resolved fields, filled in if binding is set AND the upstream agent
    # was reachable when this view was constructed. Useful for the list
    # row label ("esp32-foo → caretaker_jiezhi (default)").
    resolved_user_id: str | None = None
    resolved_template_id: str | None = None


class BindDeviceRequest(BaseModel):
    """POST /api/devices/{id}/bind body — bind to an EXISTING agent.

    Cannot create a new agent here. The operator must have created the
    target agent first via /api/agents. Enforcement: orchestrator
    looks up agent_id in agent project; 404 if missing.
    """

    agent_id: str = Field(..., min_length=1)
    # Phase 6: optional per-device interaction-mode override (overrides the
    # device's self-declared header at hub resolve time). Omit to leave unset.
    interaction_mode: InteractionMode | None = None


class UnbindDeviceResponse(BaseModel):
    device_id: str
    previously_bound_agent_id: str | None


class DiscoveryStatus(BaseModel):
    service_type: str
    service_name: str
    hostname: str
    port: int
    registered: bool
    ip: str = ""
    config_url: str = ""
    last_registered_at: str | None = None
    last_updated_at: str | None = None
    last_error: str = ""


class LiveKitRuntimeStatus(BaseModel):
    """Runtime LiveKit node advertised to LAN clients.

    ``deploy/livekit/livekit.yaml`` is source config; local dev injects
    ``rtc.node_ip`` into a generated runtime config before LiveKit starts.
    """

    node_ip: str = ""
    config_path: str = ""
    template_config_path: str = ""
    last_error: str = ""


class DeviceListResponse(BaseModel):
    devices: list[DeviceView]
    # Hub reachability. False → devices list is empty + admin should
    # surface a banner. Distinct from "no devices discovered yet".
    hub_available: bool
    discovery: DiscoveryStatus | None = None
    livekit: LiveKitRuntimeStatus | None = None
    refreshed: bool = False
