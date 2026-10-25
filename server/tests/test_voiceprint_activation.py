from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from eidolon_sdk.biz.voiceprint import (
    PolicyUpdate,
    VoicePolicyActivation,
    VoicePolicyEvidence,
    VoicePolicyApproval,
    VoicePolicyView,
    VoiceProfileEvidence,
    ExtractedSample,
    EnrollmentEvaluation,
    SpeakerExtractionReadiness,
)
from eidolon_admin_server.app.management import voiceprints_router as api


@pytest.mark.parametrize("change", [None, "not_ready", "revision", "release", "evidence"])
async def test_activation_cannot_skip_release_evidence_or_current_scope(monkeypatch, change):
    sample = ExtractedSample(
        model_id="test",
        weights_hash="a" * 64,
        preprocessing_version="v1",
        vector=[1, 0],
        speech_ms=2000,
    )
    evaluation = EnrollmentEvaluation(
        evidence_digest="b" * 64,
        recording_sha256="c" * 64,
        template=sample,
        trial=sample,
        registration_min_similarity=0.9,
        trial_min_similarity=0.9,
    )
    evidence = VoicePolicyEvidence(
        owner_id="owner",
        companion_id="c",
        revision=2,
        profiles=[VoiceProfileEvidence(profile_id="p", revision=1, evaluation=evaluation)],
    )
    approval = VoicePolicyApproval(calibration_digest="d" * 64, evidence_digest=evidence.digest())
    if change == "revision":
        evidence.revision = 3
    if change == "release":
        approval.calibration_digest = "e" * 64
    if change == "evidence":
        approval.evidence_digest = "f" * 64
    ready = (
        SpeakerExtractionReadiness(state="ready", extraction_ready=True)
        if change == "not_ready"
        else SpeakerExtractionReadiness(
            state="ready", extraction_ready=True, admission_ready=True, calibration_digest="d" * 64
        )
    )
    writes = []

    async def call(method, owner, companion, suffix, model, body=None):
        assert owner == "owner" and companion == "c"
        if method == "GET":
            assert suffix == "/policy-evidence"
            return evidence
        writes.append(VoicePolicyActivation.model_validate(body))
        return VoicePolicyView(
            companion_id="c", mode="allowlist", revision=3, admission_state="ready"
        )

    data = SimpleNamespace(get_owner_companion=AsyncMock(), voiceprint_call=call)
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(control_plane=SimpleNamespace(data=data)))
    )
    monkeypatch.setattr(api, "readiness_value", AsyncMock(return_value=ready))
    monkeypatch.setattr(api, "channel_call", AsyncMock(return_value=approval))
    if change:
        with pytest.raises(HTTPException):
            await api.policy(
                request, "owner", "c", PolicyUpdate(mode="allowlist", expected_revision=2)
            )
        assert not writes
    else:
        result = await api.policy(
            request, "owner", "c", PolicyUpdate(mode="allowlist", expected_revision=2)
        )
        assert result.admission_state == "ready" and result.readiness.admission_ready
        assert writes[0].approval == approval


def test_public_update_cannot_inject_internal_approval():
    with pytest.raises(ValueError):
        PolicyUpdate.model_validate(
            dict(
                mode="allowlist",
                expected_revision=0,
                approval={"calibration_digest": "a" * 64, "evidence_digest": "b" * 64},
            )
        )
