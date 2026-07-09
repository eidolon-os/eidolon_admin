"""Runtime identity resolve schemas."""

from __future__ import annotations

from pydantic import BaseModel


class ResolvedContext(BaseModel):
    """Minimal envelope a runtime caller needs to enter eidolon_agent."""

    owner_id: str
    companion_id: str
    device_id: str | None = None
    memory_realm_id: str
    genome_id: str
    schema_version: str
    genome_hash: str
    compiler_version: str
    interaction_mode: str | None = None


class ResolveResponse(BaseModel):
    context: ResolvedContext
