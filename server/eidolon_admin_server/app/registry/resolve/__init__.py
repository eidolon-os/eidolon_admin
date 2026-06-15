"""Resolve — runtime aggregator across all five entities.

Channel (LiveKit voice) and the web client both need the same
composite answer: "given this device_id (or user_id), tell me the
template + soul + memory MCP URL the session should use".

Without this aggregator, channel would call three sub-projects directly
(hub for device → admin for binding → memory for mcp_url). The whole
point of admin's edge-routing is that channel asks ONCE and admin
joins under the hood.

Read-only. All authoring goes through the per-entity endpoints.

Failure semantics: intolerant of partial state. If the device is
unbound, the bound agent is gone, or memory is unreachable for the
user — the endpoint returns 4xx/5xx with a precise reason. We never
silently fall back to a "default anything" — that's the silent-fallback
anti-pattern this whole Phase 29 is correcting.
"""
from .orchestrator import (
    ResolveDeviceNotBound,
    ResolveDeviceUnavailable,
    ResolveError,
    ResolveOrchestrator,
    ResolveUpstreamDown,
    ResolveUserNoActiveAgent,
    ResolveUserUnavailable,
)
from .router import router

__all__ = [
    "ResolveDeviceNotBound",
    "ResolveDeviceUnavailable",
    "ResolveError",
    "ResolveOrchestrator",
    "ResolveUpstreamDown",
    "ResolveUserNoActiveAgent",
    "ResolveUserUnavailable",
    "router",
]
