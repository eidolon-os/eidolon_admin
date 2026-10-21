"""Owner-scoped orchestration. Extraction stays in Channel; persistence stays in Data."""

import base64
import binascii
import hashlib

import httpx
from pydantic import ValidationError
from urllib.parse import quote
from fastapi import APIRouter, Request, HTTPException, Depends, Response
from eidolon_sdk.biz.voiceprint import (
    VoicePolicyView,
    PolicyUpdate,
    EnrollmentView,
    EnrollmentCreate,
    SpeakerProfileView,
    VoiceAudioRequest,
    ExtractedSample,
    EnrollmentCompletion,
    EnrollmentEvidence,
    EnrollmentEvaluation,
    EnrollmentEvaluationRequest,
    SpeakerExtractionReadiness,
)

from eidolon_admin_server.app.service_auth import require_local_api_credential
from eidolon_admin_server.app.gateway.proxy import upstream_auth_headers

router = APIRouter(
    prefix="/internal/v1/management",
    dependencies=[Depends(require_local_api_credential)],
)


async def channel_call(request, path, model, payload=None):
    service = request.app.state.registry.get("channel-provider")
    if service is None or not service.base_url:
        raise HTTPException(503, "声纹服务暂不可用，请稍后重试")
    url = (
        service.base_url.rstrip("/")
        + (service.upstream_prefix or "").rstrip("/")
        + "/v1/voiceprints/"
        + path
    )
    try:
        response = await request.app.state.http_client.request(
            "GET" if payload is None else "POST",
            url,
            headers=upstream_auth_headers(service),
            json=None if payload is None else payload.model_dump(mode="json"),
            timeout=8,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(503, "主机未确认成功，请重试原操作") from exc
    if response.status_code == 422:
        try:
            code = response.json().get("code")
        except ValueError:
            code = None
        reason = {
            "DIGITAL_SILENCE": "没有录到声音，请检查麦克风后重录",
            "SPEECH_TOO_SHORT": "有效语音太短，请连续说完一句话后重录",
            "AUDIO_TOO_SHORT": "录音太短，请完整说完一句话后重录",
            "AUDIO_CLIPPED": "录音音量过大，请稍微远离手机后重录",
        }.get(code, "录音或版本不兼容，请返回重新开始录入")
        raise HTTPException(422, reason)
    if response.status_code != 200:
        raise HTTPException(503, "声纹服务尚未准备好或正忙，请稍后重试")
    try:
        return model.model_validate(response.json())
    except (ValueError, ValidationError) as exc:
        raise HTTPException(503, "声纹服务结果无效，请稍后重试") from exc


async def extract(request, payload):
    return await channel_call(request, "extract", ExtractedSample, payload)


async def readiness_value(request):
    try:
        return await channel_call(request, "readiness", SpeakerExtractionReadiness)
    except HTTPException:
        return SpeakerExtractionReadiness(state="unavailable", extraction_ready=False)


@router.get(
    "/companions/{companion_id}/voiceprints/readiness",
    response_model=SpeakerExtractionReadiness,
)
async def readiness(request: Request, owner_id: str, companion_id: str):
    await request.app.state.control_plane.data.get_owner_companion(
        owner_id, companion_id
    )
    return await readiness_value(request)


@router.get("/companions/{companion_id}/voiceprints", response_model=VoicePolicyView)
async def view(request: Request, owner_id: str, companion_id: str):
    body = None
    result = await request.app.state.control_plane.data.voiceprint_call(
        "GET", owner_id, companion_id, "", VoicePolicyView, body
    )
    return result.model_copy(update={"readiness": await readiness_value(request)})


@router.put(
    "/companions/{companion_id}/voiceprints/policy", response_model=VoicePolicyView
)
async def policy(
    request: Request, owner_id: str, companion_id: str, payload: PolicyUpdate
):
    await request.app.state.control_plane.data.get_owner_companion(
        owner_id, companion_id
    )
    if payload.mode == "allowlist":
        raise HTTPException(409, "声纹识别仍在验证中，目前无法开启限制")
    return await request.app.state.control_plane.data.voiceprint_call(
        "PUT",
        owner_id,
        companion_id,
        "/policy",
        VoicePolicyView,
        payload.model_dump(mode="json"),
    )


@router.put(
    "/companions/{companion_id}/voiceprints/bindings/{profile_id}",
    response_model=VoicePolicyView,
)
async def bind(request: Request, owner_id: str, companion_id: str, profile_id: str):
    body = None
    return await request.app.state.control_plane.data.voiceprint_call(
        "PUT",
        owner_id,
        companion_id,
        f"/bindings/{quote(profile_id, safe='')}",
        VoicePolicyView,
        body,
    )


@router.delete(
    "/companions/{companion_id}/voiceprints/bindings/{profile_id}",
    response_model=VoicePolicyView,
)
async def unbind(request: Request, owner_id: str, companion_id: str, profile_id: str):
    body = None
    return await request.app.state.control_plane.data.voiceprint_call(
        "DELETE",
        owner_id,
        companion_id,
        f"/bindings/{quote(profile_id, safe='')}",
        VoicePolicyView,
        body,
    )


@router.delete(
    "/companions/{companion_id}/voiceprints/profiles/{profile_id}",
    response_model=VoicePolicyView,
)
async def delete_profile(
    request: Request, owner_id: str, companion_id: str, profile_id: str
):
    body = None
    return await request.app.state.control_plane.data.voiceprint_call(
        "DELETE",
        owner_id,
        companion_id,
        f"/profiles/{quote(profile_id, safe='')}",
        VoicePolicyView,
        body,
    )


@router.get(
    "/companions/{companion_id}/voiceprints/profiles/{profile_id}/recording",
    response_model=VoiceAudioRequest,
)
async def recording(
    request: Request,
    response: Response,
    owner_id: str,
    companion_id: str,
    profile_id: str,
):
    response.headers["Cache-Control"] = "no-store"
    return await request.app.state.control_plane.data.voiceprint_call(
        "GET",
        owner_id,
        companion_id,
        f"/profiles/{quote(profile_id, safe='')}/recording",
        VoiceAudioRequest,
    )


@router.post(
    "/companions/{companion_id}/voiceprints/enrollments", response_model=EnrollmentView
)
async def create_enrollment(
    request: Request, owner_id: str, companion_id: str, payload: EnrollmentCreate
):
    await request.app.state.control_plane.data.get_owner_companion(
        owner_id, companion_id
    )
    if not (await readiness_value(request)).extraction_ready:
        raise HTTPException(503, "声纹服务正在准备或不可用，请稍后重试")
    return await request.app.state.control_plane.data.voiceprint_call(
        "POST",
        owner_id,
        companion_id,
        "/enrollments",
        EnrollmentView,
        payload.model_dump(mode="json"),
    )


@router.get(
    "/companions/{companion_id}/voiceprints/enrollments/{enrollment_id}",
    response_model=EnrollmentView,
)
async def enrollment(
    request: Request, owner_id: str, companion_id: str, enrollment_id: str
):
    body = None
    return await request.app.state.control_plane.data.voiceprint_call(
        "GET",
        owner_id,
        companion_id,
        f"/enrollments/{quote(enrollment_id, safe='')}",
        EnrollmentView,
        body,
    )


@router.delete(
    "/companions/{companion_id}/voiceprints/enrollments/{enrollment_id}",
    response_model=EnrollmentView,
)
async def cancel(
    request: Request, owner_id: str, companion_id: str, enrollment_id: str
):
    body = None
    return await request.app.state.control_plane.data.voiceprint_call(
        "DELETE",
        owner_id,
        companion_id,
        f"/enrollments/{quote(enrollment_id, safe='')}",
        EnrollmentView,
        body,
    )


@router.put(
    "/companions/{companion_id}/voiceprints/enrollments/{enrollment_id}/samples/{sample_id}",
    response_model=EnrollmentView,
)
async def sample(
    request: Request,
    owner_id: str,
    companion_id: str,
    enrollment_id: str,
    sample_id: str,
    payload: VoiceAudioRequest,
):
    data = request.app.state.control_plane.data
    await data.get_owner_companion(owner_id, companion_id)
    await data.voiceprint_call(
        "GET",
        owner_id,
        companion_id,
        f"/enrollments/{quote(enrollment_id, safe='')}",
        EnrollmentView,
    )
    body = (await extract(request, payload)).model_dump(mode="json")
    return await request.app.state.control_plane.data.voiceprint_call(
        "PUT",
        owner_id,
        companion_id,
        f"/enrollments/{quote(enrollment_id, safe='')}/samples/{quote(sample_id, safe='')}",
        EnrollmentView,
        body,
    )


@router.post(
    "/companions/{companion_id}/voiceprints/enrollments/{enrollment_id}/complete",
    response_model=SpeakerProfileView,
)
async def complete(
    request: Request,
    owner_id: str,
    companion_id: str,
    enrollment_id: str,
    payload: VoiceAudioRequest,
):
    data = request.app.state.control_plane.data
    suffix = f"/enrollments/{quote(enrollment_id, safe='')}"
    current = await data.voiceprint_call(
        "GET", owner_id, companion_id, suffix, EnrollmentView
    )
    if current.state == "ready":
        profiles = await data.voiceprint_call(
            "GET", owner_id, companion_id, "", VoicePolicyView
        )
        for profile in profiles.profiles:
            if profile.enrollment_id == enrollment_id:
                return profile
        raise HTTPException(409, "这次录入已被删除或替换，请刷新声纹列表")
    evidence = await data.voiceprint_call(
        "GET", owner_id, companion_id, suffix + "/evidence", EnrollmentEvidence
    )
    if len(evidence.samples) != 3:
        raise HTTPException(409, "请先完成前三段录音")
    trial = await extract(request, payload)
    try:
        pcm = base64.b64decode(payload.pcm_base64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(422, "录音格式无效，请重录") from exc
    evaluation = await channel_call(
        request,
        "evaluate-enrollment",
        EnrollmentEvaluation,
        EnrollmentEvaluationRequest(
            samples=evidence.samples,
            trial=trial,
            recording_sha256=hashlib.sha256(pcm).hexdigest(),
        ),
    )
    body = EnrollmentCompletion(evaluation=evaluation, recording=payload).model_dump(
        mode="json"
    )
    return await data.voiceprint_call(
        "POST", owner_id, companion_id, suffix + "/complete", SpeakerProfileView, body
    )
