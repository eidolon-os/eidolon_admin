"""Voiceprint enrollment and profile schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class VoiceprintProfileView(BaseModel):
    profile_id: str
    tenant_id: str
    user_id: str
    provider: str
    model: str
    embedding_ref: str = ""
    sample_refs: list[str] = Field(default_factory=list)
    sample_rate: int = 16000
    duration_ms: int = 0
    threshold: float | None = None
    quality: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class VoiceprintStatusResponse(BaseModel):
    status: Literal["empty", "ready"]
    user_id: str
    tenant_id: str | None = None
    profile: VoiceprintProfileView | None = None


class CreateVoiceprintEnrollmentRequest(BaseModel):
    provider: str = "3d_speaker"
    model: str = "campplus_zh_16k_common"
    sample_rate: int = 16000


class VoiceprintEnrollmentResponse(BaseModel):
    enrollment_id: str
    user_id: str
    tenant_id: str
    provider: str
    model: str
    sample_rate: int
    sample_count: int
    created_at: datetime


class VoiceprintSampleResponse(BaseModel):
    enrollment_id: str
    sample_id: str
    bytes: int
    duration_ms: int
    sample_rate: int
    channels: int


class CompleteVoiceprintEnrollmentResponse(BaseModel):
    profile: VoiceprintProfileView


class VoiceprintTestComparison(BaseModel):
    sample_ref: str
    score: float
    prediction: Literal["yes", "no", "unknown"]
    latency_ms: int


class VoiceprintTestAudioInfo(BaseModel):
    bytes: int
    duration_ms: int
    sample_rate: int
    channels: int


class VoiceprintTestResponse(BaseModel):
    profile_id: str
    provider: str
    model: str
    threshold: float
    matched: bool
    verdict: Literal["pass", "uncertain", "fail"]
    best_score: float
    average_score: float
    latency_ms: int
    test_audio: VoiceprintTestAudioInfo
    comparisons: list[VoiceprintTestComparison]
