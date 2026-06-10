"""HTTP routes for per-user voiceprint enrollment."""

from __future__ import annotations

import tempfile
from functools import partial
from pathlib import Path

import anyio
from fastapi import APIRouter, HTTPException, Request, Response, status

from ..schemas.voiceprint import (
    CompleteVoiceprintEnrollmentResponse,
    CreateVoiceprintEnrollmentRequest,
    VoiceprintEnrollmentResponse,
    VoiceprintSampleResponse,
    VoiceprintStatusResponse,
    VoiceprintTestAudioInfo,
    VoiceprintTestComparison,
    VoiceprintTestResponse,
)
from .repository import VoiceprintEmbedding, VoiceprintStore, inspect_wav_bytes
from .verifier import ModelScopeVoiceprintVerifier

router = APIRouter(prefix="/users/{user_id}/voiceprint", tags=["voiceprints"])
_DEFAULT_THRESHOLD = 0.31


def _store(request: Request) -> VoiceprintStore:
    store = getattr(request.app.state, "voiceprint_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="voiceprint store not configured")
    return store


def _verifier(request: Request) -> ModelScopeVoiceprintVerifier:
    verifier = getattr(request.app.state, "voiceprint_verifier", None)
    if verifier is not None:
        return verifier
    model_dir = getattr(request.app.state, "voiceprint_model_dir", None)
    if model_dir is None:
        raise HTTPException(status_code=503, detail="voiceprint model dir not configured")
    verifier = ModelScopeVoiceprintVerifier(model_dir)
    request.app.state.voiceprint_verifier = verifier
    return verifier


async def _build_and_save_embedding(
    *,
    request: Request,
    store: VoiceprintStore,
    profile,
):
    sample_paths = store.sample_paths_for_profile(profile)
    if not sample_paths:
        raise HTTPException(status_code=400, detail="voiceprint profile has no samples")
    if profile.provider not in {"3d_speaker", "noop"}:
        return profile
    try:
        embedding = await anyio.to_thread.run_sync(
            partial(
                _verifier(request).build_profile_embedding,
                enrollment_wavs=sample_paths,
            ),
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return store.save_embedding(
        profile=profile,
        embedding=VoiceprintEmbedding(
            profile_id=profile.profile_id,
            tenant_id=profile.tenant_id,
            user_id=profile.user_id,
            provider="3d_speaker",
            model="campplus_zh_16k_common",
            vector=embedding.vector,
            dim=embedding.dim,
            dtype=embedding.dtype,
            pooling=embedding.pooling,
            sample_refs=profile.sample_refs,
            sample_rate=profile.sample_rate,
            metadata={
                "latency_ms": embedding.latency_ms,
                "sample_count": embedding.sample_count,
            },
        ),
    )


async def _user_context(user_id: str, request: Request) -> tuple[str, str]:
    user_orch = getattr(request.app.state, "user_orchestrator", None)
    if user_orch is None:
        raise HTTPException(status_code=503, detail="user orchestrator unavailable")
    try:
        view = await user_orch.get_user(user_id)
    except Exception as exc:  # noqa: BLE001 - preserve router boundary
        status_code = getattr(exc, "status_code", 500)
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    return view.spec.tenant_id, view.spec.user_id


@router.get("", response_model=VoiceprintStatusResponse)
async def get_voiceprint(user_id: str, request: Request) -> VoiceprintStatusResponse:
    tenant_id, resolved_user_id = await _user_context(user_id, request)
    profile = _store(request).get_profile(tenant_id=tenant_id, user_id=resolved_user_id)
    if profile is None:
        return VoiceprintStatusResponse(
            status="empty",
            user_id=resolved_user_id,
            tenant_id=tenant_id,
        )
    return VoiceprintStatusResponse(
        status="ready",
        user_id=resolved_user_id,
        tenant_id=tenant_id,
        profile=profile.to_view(),
    )


@router.post(
    "/enrollments",
    response_model=VoiceprintEnrollmentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_enrollment(
    user_id: str,
    body: CreateVoiceprintEnrollmentRequest,
    request: Request,
) -> VoiceprintEnrollmentResponse:
    tenant_id, resolved_user_id = await _user_context(user_id, request)
    enrollment = _store(request).create_enrollment(
        tenant_id=tenant_id,
        user_id=resolved_user_id,
        provider=body.provider,
        model=body.model,
        sample_rate=body.sample_rate,
    )
    return enrollment.to_response(sample_count=0)


@router.post(
    "/enrollments/{enrollment_id}/samples",
    response_model=VoiceprintSampleResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_sample(
    user_id: str,
    enrollment_id: str,
    request: Request,
) -> VoiceprintSampleResponse:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
    if content_type not in {"audio/wav", "audio/x-wav", "audio/wave"}:
        raise HTTPException(status_code=415, detail="expected audio/wav")
    tenant_id, resolved_user_id = await _user_context(user_id, request)
    wav_bytes = await request.body()
    if not wav_bytes:
        raise HTTPException(status_code=400, detail="empty sample")
    return _store(request).add_sample(
        enrollment_id=enrollment_id,
        tenant_id=tenant_id,
        user_id=resolved_user_id,
        wav_bytes=wav_bytes,
    )


@router.post(
    "/enrollments/{enrollment_id}/complete",
    response_model=CompleteVoiceprintEnrollmentResponse,
)
async def complete_enrollment(
    user_id: str,
    enrollment_id: str,
    request: Request,
) -> CompleteVoiceprintEnrollmentResponse:
    tenant_id, resolved_user_id = await _user_context(user_id, request)
    store = _store(request)
    profile = store.complete_enrollment(
        enrollment_id=enrollment_id,
        tenant_id=tenant_id,
        user_id=resolved_user_id,
    )
    profile = await _build_and_save_embedding(request=request, store=store, profile=profile)
    return CompleteVoiceprintEnrollmentResponse(profile=profile.to_view())


@router.post("/embedding/rebuild", response_model=CompleteVoiceprintEnrollmentResponse)
async def rebuild_embedding(
    user_id: str,
    request: Request,
) -> CompleteVoiceprintEnrollmentResponse:
    tenant_id, resolved_user_id = await _user_context(user_id, request)
    store = _store(request)
    profile = store.get_profile(tenant_id=tenant_id, user_id=resolved_user_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="voiceprint profile not found")
    profile = await _build_and_save_embedding(request=request, store=store, profile=profile)
    return CompleteVoiceprintEnrollmentResponse(profile=profile.to_view())


@router.post("/test", response_model=VoiceprintTestResponse)
async def test_voiceprint(user_id: str, request: Request) -> VoiceprintTestResponse:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
    if content_type not in {"audio/wav", "audio/x-wav", "audio/wave"}:
        raise HTTPException(status_code=415, detail="expected audio/wav")

    tenant_id, resolved_user_id = await _user_context(user_id, request)
    store = _store(request)
    profile = store.get_profile(tenant_id=tenant_id, user_id=resolved_user_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="voiceprint profile not found")

    wav_bytes = await request.body()
    if not wav_bytes:
        raise HTTPException(status_code=400, detail="empty sample")
    info = inspect_wav_bytes(wav_bytes)
    if info["sample_rate"] != profile.sample_rate:
        raise HTTPException(status_code=400, detail="sample_rate mismatch")
    if info["channels"] != 1:
        raise HTTPException(status_code=400, detail="expected mono WAV")
    if info["sample_width"] != 2:
        raise HTTPException(status_code=400, detail="expected 16-bit PCM WAV")

    sample_paths = store.sample_paths_for_profile(profile)
    if not sample_paths:
        raise HTTPException(status_code=400, detail="voiceprint profile has no samples")

    threshold = profile.threshold or _DEFAULT_THRESHOLD
    verifier = _verifier(request)
    with tempfile.TemporaryDirectory(prefix="eidolon-voiceprint-test-") as tmp:
        test_path = Path(tmp) / "test.wav"
        test_path.write_bytes(wav_bytes)
        try:
            report = await anyio.to_thread.run_sync(
                partial(
                    verifier.compare,
                    test_wav=test_path,
                    enrollment_wavs=sample_paths,
                    threshold=threshold,
                    root=store.root,
                ),
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    return VoiceprintTestResponse(
        profile_id=profile.profile_id,
        provider="3d_speaker",
        model="campplus_zh_16k_common",
        threshold=report.threshold,
        matched=report.matched,
        verdict=report.verdict,  # type: ignore[arg-type]
        best_score=report.best_score,
        average_score=report.average_score,
        latency_ms=report.latency_ms,
        test_audio=VoiceprintTestAudioInfo(
            bytes=len(wav_bytes),
            duration_ms=int(info["duration_ms"]),
            sample_rate=int(info["sample_rate"]),
            channels=int(info["channels"]),
        ),
        comparisons=[
            VoiceprintTestComparison(
                sample_ref=item.sample_ref,
                score=item.score,
                prediction=item.prediction,  # type: ignore[arg-type]
                latency_ms=item.latency_ms,
            )
            for item in report.comparisons
        ],
    )


@router.delete(
    "/enrollments/{enrollment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def cancel_enrollment(
    user_id: str,
    enrollment_id: str,
    request: Request,
) -> Response:
    tenant_id, resolved_user_id = await _user_context(user_id, request)
    _store(request).delete_enrollment(
        enrollment_id=enrollment_id,
        tenant_id=tenant_id,
        user_id=resolved_user_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_voiceprint(user_id: str, request: Request) -> Response:
    tenant_id, resolved_user_id = await _user_context(user_id, request)
    _store(request).delete_profile(tenant_id=tenant_id, user_id=resolved_user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
