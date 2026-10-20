"""Owner-scoped orchestration. Extraction stays in Channel; persistence stays in Data."""

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
)

from eidolon_admin_server.app.service_auth import require_local_api_credential
from eidolon_admin_server.app.gateway.proxy import upstream_auth_headers

router = APIRouter(
    prefix="/internal/v1/management",
    dependencies=[Depends(require_local_api_credential)],
)


async def extract(request, payload):
    service = request.app.state.registry.get("channel-provider")
    if service is None or not service.base_url:
        raise HTTPException(503, "voice extraction unavailable")
    url = (
        service.base_url.rstrip("/")
        + (service.upstream_prefix or "").rstrip("/")
        + "/v1/voiceprints/extract"
    )
    try:
        response = await request.app.state.http_client.post(
            url,
            headers=upstream_auth_headers(service),
            json=payload.model_dump(mode="json"),
            timeout=25,
        )
    except Exception as exc:
        raise HTTPException(503, "voice extraction unavailable") from exc
    if response.status_code == 422:
        raise HTTPException(422, "需要更清晰、更长的语音，请重新录制")
    if response.status_code != 200:
        raise HTTPException(503, "voice extraction unavailable")
    return ExtractedSample.model_validate(response.json()).model_dump(mode="json")


@router.get("/companions/{companion_id}/voiceprints", response_model=VoicePolicyView)
async def view(request: Request, owner_id: str, companion_id: str):
    body = None
    return await request.app.state.control_plane.data.voiceprint_call(
        "GET", owner_id, companion_id, "", VoicePolicyView, body
    )


@router.put(
    "/companions/{companion_id}/voiceprints/policy", response_model=VoicePolicyView
)
async def policy(
    request: Request, owner_id: str, companion_id: str, payload: PolicyUpdate
):
    body = payload.model_dump(mode="json")
    return await request.app.state.control_plane.data.voiceprint_call(
        "PUT", owner_id, companion_id, "/policy", VoicePolicyView, body
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


@router.get("/companions/{companion_id}/voiceprints/profiles/{profile_id}/recording",
            response_model=VoiceAudioRequest)
async def recording(request: Request, response: Response, owner_id: str, companion_id: str, profile_id: str):
    response.headers["Cache-Control"] = "no-store"
    return await request.app.state.control_plane.data.voiceprint_call(
        "GET", owner_id, companion_id,
        f"/profiles/{quote(profile_id, safe='')}/recording", VoiceAudioRequest)


@router.post(
    "/companions/{companion_id}/voiceprints/enrollments", response_model=EnrollmentView
)
async def create_enrollment(
    request: Request, owner_id: str, companion_id: str, payload: EnrollmentCreate
):
    body = payload.model_dump(mode="json")
    return await request.app.state.control_plane.data.voiceprint_call(
        "POST", owner_id, companion_id, "/enrollments", EnrollmentView, body
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
    body = await extract(request, payload)
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
    await data.get_owner_companion(owner_id, companion_id)
    await data.voiceprint_call(
        "GET",
        owner_id,
        companion_id,
        f"/enrollments/{quote(enrollment_id, safe='')}",
        EnrollmentView,
    )
    sample = await extract(request, payload)
    body = EnrollmentCompletion(sample=ExtractedSample.model_validate(sample),
                                recording=payload).model_dump(mode="json")
    return await request.app.state.control_plane.data.voiceprint_call(
        "POST",
        owner_id,
        companion_id,
        f"/enrollments/{quote(enrollment_id, safe='')}/complete",
        SpeakerProfileView,
        body,
    )
