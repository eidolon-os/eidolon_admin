"""Voiceprint product routes authenticate the Controller through the existing boundary."""

from urllib.parse import quote
from fastapi import Header, Response
from eidolon_sdk.biz.voiceprint import (
    VoicePolicyView,
    PolicyUpdate,
    EnrollmentView,
    EnrollmentCreate,
    SpeakerProfileView,
    VoiceAudioRequest,
    SpeakerExtractionReadiness,
    VoiceMatchTestResult,
)

from .backend import ManagementBackendError


def register_voiceprint_routes(router, backend, authenticated_owner, refused):
    async def invoke(authorization, companion_id, suffix, method, payload):
        owner = await authenticated_owner(authorization)
        try:
            return await backend.voiceprint_call(
                owner_id=owner,
                companion_id=companion_id,
                suffix=suffix,
                method=method,
                payload=payload,
            )
        except ManagementBackendError as exc:
            raise refused(exc) from exc

    @router.post(
        "/companions/{companion_id}/voiceprints/match-test",
        response_model=VoiceMatchTestResult,
    )
    async def match_test(
        companion_id: str,
        payload: VoiceAudioRequest,
        response: Response,
        authorization: str | None = Header(default=None),
    ):
        response.headers["Cache-Control"] = "no-store"
        return await invoke(
            authorization,
            companion_id,
            "/match-test",
            "POST",
            payload.model_dump(mode="json"),
        )

    @router.get(
        "/companions/{companion_id}/voiceprints", response_model=VoicePolicyView
    )
    async def view(companion_id: str, authorization: str | None = Header(default=None)):
        return await invoke(authorization, companion_id, "", "GET", None)

    @router.get(
        "/companions/{companion_id}/voiceprints/readiness",
        response_model=SpeakerExtractionReadiness,
    )
    async def readiness(
        companion_id: str, authorization: str | None = Header(default=None)
    ):
        return await invoke(authorization, companion_id, "/readiness", "GET", None)

    @router.get(
        "/companions/{companion_id}/voiceprints/profiles/{profile_id}/recording",
        response_model=VoiceAudioRequest,
    )
    async def recording(
        companion_id: str,
        profile_id: str,
        response: Response,
        authorization: str | None = Header(default=None),
    ):
        response.headers["Cache-Control"] = "no-store"
        return await invoke(
            authorization,
            companion_id,
            f"/profiles/{quote(profile_id, safe='')}/recording",
            "GET",
            None,
        )

    @router.put(
        "/companions/{companion_id}/voiceprints/policy", response_model=VoicePolicyView
    )
    async def policy(
        companion_id: str,
        payload: PolicyUpdate,
        authorization: str | None = Header(default=None),
    ):
        return await invoke(
            authorization,
            companion_id,
            "/policy",
            "PUT",
            payload.model_dump(mode="json"),
        )

    @router.put(
        "/companions/{companion_id}/voiceprints/bindings/{profile_id}",
        response_model=VoicePolicyView,
    )
    async def bind(
        companion_id: str,
        profile_id: str,
        authorization: str | None = Header(default=None),
    ):
        return await invoke(
            authorization,
            companion_id,
            f"/bindings/{quote(profile_id, safe='')}",
            "PUT",
            None,
        )

    @router.delete(
        "/companions/{companion_id}/voiceprints/bindings/{profile_id}",
        response_model=VoicePolicyView,
    )
    async def unbind(
        companion_id: str,
        profile_id: str,
        authorization: str | None = Header(default=None),
    ):
        return await invoke(
            authorization,
            companion_id,
            f"/bindings/{quote(profile_id, safe='')}",
            "DELETE",
            None,
        )

    @router.delete(
        "/companions/{companion_id}/voiceprints/profiles/{profile_id}",
        response_model=VoicePolicyView,
    )
    async def delete_profile(
        companion_id: str,
        profile_id: str,
        authorization: str | None = Header(default=None),
    ):
        return await invoke(
            authorization,
            companion_id,
            f"/profiles/{quote(profile_id, safe='')}",
            "DELETE",
            None,
        )

    @router.post(
        "/companions/{companion_id}/voiceprints/enrollments",
        response_model=EnrollmentView,
    )
    async def create_enrollment(
        companion_id: str,
        payload: EnrollmentCreate,
        authorization: str | None = Header(default=None),
    ):
        return await invoke(
            authorization,
            companion_id,
            "/enrollments",
            "POST",
            payload.model_dump(mode="json"),
        )

    @router.get(
        "/companions/{companion_id}/voiceprints/enrollments/{enrollment_id}",
        response_model=EnrollmentView,
    )
    async def enrollment(
        companion_id: str,
        enrollment_id: str,
        authorization: str | None = Header(default=None),
    ):
        return await invoke(
            authorization,
            companion_id,
            f"/enrollments/{quote(enrollment_id, safe='')}",
            "GET",
            None,
        )

    @router.delete(
        "/companions/{companion_id}/voiceprints/enrollments/{enrollment_id}",
        response_model=EnrollmentView,
    )
    async def cancel(
        companion_id: str,
        enrollment_id: str,
        authorization: str | None = Header(default=None),
    ):
        return await invoke(
            authorization,
            companion_id,
            f"/enrollments/{quote(enrollment_id, safe='')}",
            "DELETE",
            None,
        )

    @router.put(
        "/companions/{companion_id}/voiceprints/enrollments/{enrollment_id}/samples/{sample_id}",
        response_model=EnrollmentView,
    )
    async def sample(
        companion_id: str,
        enrollment_id: str,
        sample_id: str,
        payload: VoiceAudioRequest,
        authorization: str | None = Header(default=None),
    ):
        return await invoke(
            authorization,
            companion_id,
            f"/enrollments/{quote(enrollment_id, safe='')}/samples/{quote(sample_id, safe='')}",
            "PUT",
            payload.model_dump(mode="json"),
        )

    @router.post(
        "/companions/{companion_id}/voiceprints/enrollments/{enrollment_id}/complete",
        response_model=SpeakerProfileView,
    )
    async def complete(
        companion_id: str,
        enrollment_id: str,
        payload: VoiceAudioRequest,
        authorization: str | None = Header(default=None),
    ):
        return await invoke(
            authorization,
            companion_id,
            f"/enrollments/{quote(enrollment_id, safe='')}/complete",
            "POST",
            payload.model_dump(mode="json"),
        )
