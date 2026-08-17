"""Admin's own view of eidolond's service facts.

Admin owns its consumer DTOs; it does not import eidolond's models. Only the
fields a Host operator acts on are carried over, so eidolond can grow its wire
contract without silently changing what Admin shows.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

RuntimeState = Literal[
    "unknown", "inactive", "starting", "ready", "degraded", "blocked", "failed"
]

MutationOperation = Literal["restart", "enable", "disable"]


class HostServiceEndpoint(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    endpoint_id: str
    protocol: str
    address: str
    contract: str


class HostService(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    service_id: str
    required: bool
    enabled: bool
    # eidolond mutations are compare-and-swap; the caller must echo the revision
    # it acted on, so this is part of the view rather than an internal detail.
    revision: int = Field(ge=1)
    runtime_state: RuntimeState
    detail: str | None = None
    observed_at: datetime
    endpoints: tuple[HostServiceEndpoint, ...] = ()


class HostMeasurement(BaseModel):
    """One reading the Host took of itself, or the absence of one.

    ``value`` is null when the Host could not take the reading. A consumer
    that treats null as zero reports a healthy disk on a machine that could
    not read its disk — which is the failure this whole shape exists to stop.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    value: float | None = None
    unit: str = ""
    capacity: float | None = None
    unavailable_reason: str | None = None


class HostVitals(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observed_at: str
    measurements: tuple[HostMeasurement, ...] = ()


class HostServicePage(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    driver: str
    services: tuple[HostService, ...] = ()


class HostServiceMutationResult(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    service_id: str
    operation: MutationOperation
    enabled: bool
    revision: int = Field(ge=1)
    audit_position: int = Field(ge=1)
    replayed: bool
