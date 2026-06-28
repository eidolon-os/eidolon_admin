"""Resolve device-bound runtime identity for callers."""

from __future__ import annotations

from pydantic import BaseModel


class ResolvedContext(BaseModel):
    """Everything a runtime session needs in one envelope."""

    owner_id: str
    companion_id: str
    device_id: str
    memory_realm_id: str
    genome_id: str
    interaction_mode: str | None = None


class ResolveDeviceResponse(BaseModel):
    """GET /api/resolve/device/{device_id} response."""

    context: ResolvedContext
