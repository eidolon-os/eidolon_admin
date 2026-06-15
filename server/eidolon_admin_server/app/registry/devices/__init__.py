"""Devices — physical access points, bound to existing Agents.

Differences from the Phase 25 ``app/devices/`` module being deleted in
this phase:

  - Old model: device "owned" agents — POST /api/devices/{id}/agents
    created a new agent under the device. ``active_agent_id`` was a
    device attribute.
  - New model: agents are independent entities (29.F). Devices are
    bound to ALREADY-CREATED agents via ``POST /api/devices/{id}/bind``.
    A device with no binding is "approved but not configured" — voice
    sessions for it must hard-reject (412), no fallback.

Layout (4-layer, matches other registry modules):
    repository.py     HubClient (HTTP→hub) + DeviceBindingRepository (KV)
    orchestrator.py   list / bind / unbind / approve / unregister
                      with cascade
    router.py         FastAPI HTTP

Boundary: admin never touches hub's devices.json directly — all goes
through hub's REST surface (extended in 29.B.3 with DELETE).
"""
from .orchestrator import (
    DeviceAgentMismatch,
    DeviceBadRequest,
    DeviceDisabled,
    DeviceError,
    DeviceHubDown,
    DeviceNotApproved,
    DeviceNotFound,
    DeviceOrchestrator,
)
from .repository import (
    DeviceBindingRepository,
    HubDeviceClient,
)
from .router import router

__all__ = [
    "DeviceAgentMismatch",
    "DeviceBadRequest",
    "DeviceDisabled",
    "DeviceBindingRepository",
    "DeviceError",
    "DeviceHubDown",
    "DeviceNotApproved",
    "DeviceNotFound",
    "DeviceOrchestrator",
    "HubDeviceClient",
    "router",
]
