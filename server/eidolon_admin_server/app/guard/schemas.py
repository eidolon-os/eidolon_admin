"""Admin API models for Guard ownership and lifecycle."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

JsonDict = dict[str, object]


class GuardBindingView(BaseModel):
    binding_id: str
    owner_id: str
    guard_companion_id: str
    device_id: str
    state: str
    policy_id: str
    config_revision: int
    config_json: JsonDict = Field(default_factory=dict)
    runtime_revision: int
    runtime_config_json: JsonDict = Field(default_factory=dict)
    desired_runtime_state: str
    status_json: JsonDict = Field(default_factory=dict)
    activated_at: datetime | None = None
    disabled_at: datetime | None = None
    revoked_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class GuardBindingListResponse(BaseModel):
    bindings: list[GuardBindingView] = Field(default_factory=list)


class GuardClaimRequest(BaseModel):
    device_id: str
    companion_id: str | None = None
    display_name: str = "ATK Guard"
    policy_id: str = "silent_presence"
    config_json: JsonDict = Field(default_factory=dict)
    replace: bool = False


class GuardConfigUpdateRequest(BaseModel):
    """Full replacement of an active binding's strict policy configuration."""

    expected_revision: int = Field(ge=1)
    config_json: JsonDict = Field(default_factory=dict)


class GuardRuntimeConfigUpdateRequest(BaseModel):
    """Full replacement of local Guard runtime parameters, not Hub policy."""

    expected_revision: int = Field(ge=1)
    runtime_config_json: JsonDict = Field(default_factory=dict)


class OwnerFaceProfileDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    model_id: Literal["esp-who-human-face-recognition-v1"] = (
        "esp-who-human-face-recognition-v1"
    )
    preprocessing_version: Literal["rgb565-be-qvga-v1"] = "rgb565-be-qvga-v1"


class OwnerFaceReferenceView(BaseModel):
    reference_id: str
    pose: str
    sha256: str
    size_bytes: int
    content_type: str


class OwnerFaceProfileView(BaseModel):
    profile_revision_id: str
    profile_id: str
    owner_id: str
    revision: int
    state: str
    desired_state: str
    model_id: str | None = None
    preprocessing_version: str | None = None
    references: list[OwnerFaceReferenceView] = Field(default_factory=list)
    activated_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class OwnerFaceDeliveryView(BaseModel):
    delivery_id: str
    binding_id: str
    device_id: str
    profile_id: str
    profile_revision: int
    desired_state: str
    status: str
    command_id: str | None = None
    attempt_count: int
    last_error: str
    applied_at: datetime | None = None
    updated_at: datetime


class OwnerFaceProfileStatusResponse(BaseModel):
    desired: OwnerFaceProfileView | None = None
    deliveries: list[OwnerFaceDeliveryView] = Field(default_factory=list)
