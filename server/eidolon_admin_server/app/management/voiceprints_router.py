"""Owner-scoped orchestration. Extraction stays in Channel; persistence stays in Data."""

import base64
import binascii
import hashlib
from time import perf_counter

import httpx
from pydantic import ValidationError
from urllib.parse import quote
from fastapi import APIRouter, Request, HTTPException, Depends, Response
from eidolon_sdk.biz.voiceprint import (
    VoiceprintCollection,
    VoiceprintAssignment,
    PersonVoiceTemplates,
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
    SpeakerComparisonRequest,
    SpeakerComparisonResult,
    VoiceMatchTestResult,
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
            "ENROLLMENT_VERSION_UNSUPPORTED": "声纹版本与当前模型不兼容，请重新录入对应声音",
            "ENROLLMENT_VALIDATION_REQUIRED": "已有录音未通过一致性验证，请在管理声纹中重新录入",
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
    "/voiceprints/readiness",
    response_model=SpeakerExtractionReadiness,
)
async def readiness(request: Request, owner_id: str):
    await request.app.state.control_plane.data.voiceprint_call(
        "GET", owner_id, "", VoiceprintCollection
    )
    return await readiness_value(request)


@router.delete(
    "/voiceprints/profiles/{profile_id}",
    response_model=VoiceprintCollection,
)
async def delete_profile(request: Request, owner_id: str, profile_id: str):
    body = None
    return await request.app.state.control_plane.data.voiceprint_call(
        "DELETE",
        owner_id,
        f"/profiles/{quote(profile_id, safe='')}",
        VoiceprintCollection,
        body,
    )


@router.get(
    "/voiceprints/profiles/{profile_id}/recording",
    response_model=VoiceAudioRequest,
)
async def recording(
    request: Request,
    response: Response,
    owner_id: str,
    profile_id: str,
):
    response.headers["Cache-Control"] = "no-store"
    return await request.app.state.control_plane.data.voiceprint_call(
        "GET",
        owner_id,
        f"/profiles/{quote(profile_id, safe='')}/recording",
        VoiceAudioRequest,
    )


@router.post("/voiceprints/enrollments", response_model=EnrollmentView)
async def create_enrollment(request: Request, owner_id: str, payload: EnrollmentCreate):
    await request.app.state.control_plane.data.voiceprint_call(
        "GET", owner_id, "", VoiceprintCollection
    )
    if not (await readiness_value(request)).extraction_ready:
        raise HTTPException(503, "声纹服务正在准备或不可用，请稍后重试")
    return await request.app.state.control_plane.data.voiceprint_call(
        "POST",
        owner_id,
        "/enrollments",
        EnrollmentView,
        payload.model_dump(mode="json"),
    )


@router.get(
    "/voiceprints/enrollments/{enrollment_id}",
    response_model=EnrollmentView,
)
async def enrollment(request: Request, owner_id: str, enrollment_id: str):
    body = None
    return await request.app.state.control_plane.data.voiceprint_call(
        "GET",
        owner_id,
        f"/enrollments/{quote(enrollment_id, safe='')}",
        EnrollmentView,
        body,
    )


@router.delete(
    "/voiceprints/enrollments/{enrollment_id}",
    response_model=EnrollmentView,
)
async def cancel(request: Request, owner_id: str, enrollment_id: str):
    body = None
    return await request.app.state.control_plane.data.voiceprint_call(
        "DELETE",
        owner_id,
        f"/enrollments/{quote(enrollment_id, safe='')}",
        EnrollmentView,
        body,
    )


@router.put(
    "/voiceprints/enrollments/{enrollment_id}/samples/{sample_id}",
    response_model=EnrollmentView,
)
async def sample(
    request: Request,
    owner_id: str,
    enrollment_id: str,
    sample_id: str,
    payload: VoiceAudioRequest,
):
    data = request.app.state.control_plane.data
    await data.voiceprint_call("GET", owner_id, "", VoiceprintCollection)
    await data.voiceprint_call(
        "GET",
        owner_id,
        f"/enrollments/{quote(enrollment_id, safe='')}",
        EnrollmentView,
    )
    body = (await extract(request, payload)).model_dump(mode="json")
    return await request.app.state.control_plane.data.voiceprint_call(
        "PUT",
        owner_id,
        f"/enrollments/{quote(enrollment_id, safe='')}/samples/{quote(sample_id, safe='')}",
        EnrollmentView,
        body,
    )


@router.post(
    "/voiceprints/enrollments/{enrollment_id}/complete",
    response_model=SpeakerProfileView,
)
async def complete(
    request: Request,
    owner_id: str,
    enrollment_id: str,
    payload: VoiceAudioRequest,
):
    data = request.app.state.control_plane.data
    suffix = f"/enrollments/{quote(enrollment_id, safe='')}"
    current = await data.voiceprint_call("GET", owner_id, suffix, EnrollmentView)
    if current.state == "ready":
        profiles = await data.voiceprint_call("GET", owner_id, "", VoiceprintCollection)
        for profile in profiles.profiles:
            if profile.enrollment_id == enrollment_id:
                return profile
        raise HTTPException(409, "这次录入已被删除或替换，请刷新声纹列表")
    evidence = await data.voiceprint_call(
        "GET", owner_id, suffix + "/evidence", EnrollmentEvidence
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
    saved = await data.voiceprint_call(
        "POST", owner_id, suffix + "/complete", SpeakerProfileView, body
    )
    return saved


@router.get("/voiceprints", response_model=VoiceprintCollection)
async def view(request: Request, owner_id: str):
    result = await request.app.state.control_plane.data.voiceprint_call(
        "GET", owner_id, "", VoiceprintCollection
    )
    return result.model_copy(update={"readiness": await readiness_value(request)})


@router.put(
    "/voiceprints/profiles/{profile_id}/person", response_model=SpeakerProfileView
)
async def assign(
    request: Request, owner_id: str, profile_id: str, payload: VoiceprintAssignment
):
    return await request.app.state.control_plane.data.voiceprint_call(
        "PUT",
        owner_id,
        f"/profiles/{quote(profile_id, safe='')}/person",
        SpeakerProfileView,
        payload.model_dump(mode="json"),
    )


@router.post(
    "/voiceprints/persons/{person_id}/match-test", response_model=VoiceMatchTestResult
)
async def match_test(
    request: Request,
    response: Response,
    owner_id: str,
    person_id: str,
    payload: VoiceAudioRequest,
):
    response.headers["Cache-Control"] = "no-store"
    started = perf_counter()
    data = request.app.state.control_plane.data
    suffix = f"/persons/{quote(person_id, safe='')}/templates"
    snapshot = await data.voiceprint_call("GET", owner_id, suffix, PersonVoiceTemplates)
    if snapshot.owner_id != owner_id or snapshot.person_id != person_id:
        raise HTTPException(404, "用户不存在")
    if not snapshot.templates:
        raise HTTPException(409, "请先为此用户录入或关联声纹")
    extraction_started = perf_counter()
    probe = await extract(request, payload)
    extraction_ms = (perf_counter() - extraction_started) * 1000
    result = await channel_call(
        request,
        "compare",
        SpeakerComparisonResult,
        SpeakerComparisonRequest(probe=probe, templates=snapshot.templates),
    )
    current = await data.voiceprint_call("GET", owner_id, suffix, PersonVoiceTemplates)
    if current != snapshot:
        raise HTTPException(409, "用户或声纹已变化，请重新测试")
    expected = {(t.profile_id, t.revision) for t in snapshot.templates}
    if (
        len(result.scores) != len(expected)
        or {(s.profile_id, s.revision) for s in result.scores} != expected
    ):
        raise HTTPException(503, "声纹服务结果无效")
    return VoiceMatchTestResult(
        **result.model_dump(),
        person_id=person_id,
        person_revision=snapshot.person_revision,
        audio_ms=len(base64.b64decode(payload.pcm_base64, validate=True)) // 32,
        extraction_ms=extraction_ms,
        elapsed_ms=(perf_counter() - started) * 1000,
    )
