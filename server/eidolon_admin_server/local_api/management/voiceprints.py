"""Voiceprint product routes authenticate the Controller through the existing boundary."""

from urllib.parse import quote
from fastapi import Header, Response
from eidolon_sdk.biz.voiceprint import (
    VoiceprintCollection,
    EnrollmentView,
    EnrollmentCreate,
    SpeakerProfileView,
    VoiceAudioRequest,
    VoiceCaptureSession,
    SpeakerExtractionReadiness,
    VoiceMatchTestResult,
    ChannelCalibrationUpload,
    ChannelCalibrationSample,
    ChannelCalibrationCollection,
)

from .backend import ManagementBackendError


def register_voiceprint_routes(router, backend, authenticated_owner, refused):
    async def invoke(authorization, suffix, method, payload):
        owner = await authenticated_owner(authorization)
        try:
            return await backend.voiceprint_call(
                owner_id=owner,
                suffix=suffix,
                method=method,
                payload=payload,
            )
        except ManagementBackendError as exc:
            raise refused(exc) from exc

    @router.post(
        "/voiceprints/persons/{person_id}/match-test",
        response_model=VoiceMatchTestResult,
    )
    async def match_test(
        person_id: str,
        payload: VoiceAudioRequest,
        response: Response,
        authorization: str | None = Header(default=None),
    ):
        response.headers["Cache-Control"] = "no-store"
        return await invoke(
            authorization,
            f"/persons/{quote(person_id, safe='')}/match-test",
            "POST",
            payload.model_dump(mode="json"),
        )

    @router.get("/voiceprints", response_model=VoiceprintCollection)
    async def view(authorization: str | None = Header(default=None)):
        return await invoke(authorization, "", "GET", None)

    @router.get(
        "/voiceprints/readiness",
        response_model=SpeakerExtractionReadiness,
    )
    async def readiness(authorization: str | None = Header(default=None)):
        return await invoke(authorization, "/readiness", "GET", None)

    @router.get(
        "/voiceprints/profiles/{profile_id}/recording",
        response_model=VoiceAudioRequest,
    )
    async def recording(
        profile_id: str,
        response: Response,
        authorization: str | None = Header(default=None),
    ):
        response.headers["Cache-Control"] = "no-store"
        return await invoke(
            authorization,
            f"/profiles/{quote(profile_id, safe='')}/recording",
            "GET",
            None,
        )

    @router.delete(
        "/voiceprints/profiles/{profile_id}",
        response_model=VoiceprintCollection,
    )
    async def delete_profile(
        profile_id: str,
        authorization: str | None = Header(default=None),
    ):
        return await invoke(
            authorization,
            f"/profiles/{quote(profile_id, safe='')}",
            "DELETE",
            None,
        )

    @router.post(
        "/voiceprints/enrollments",
        response_model=EnrollmentView,
    )
    async def create_enrollment(
        payload: EnrollmentCreate,
        authorization: str | None = Header(default=None),
    ):
        return await invoke(
            authorization,
            "/enrollments",
            "POST",
            payload.model_dump(mode="json"),
        )

    @router.get(
        "/voiceprints/enrollments/{enrollment_id}",
        response_model=EnrollmentView,
    )
    async def enrollment(
        enrollment_id: str,
        authorization: str | None = Header(default=None),
    ):
        return await invoke(
            authorization,
            f"/enrollments/{quote(enrollment_id, safe='')}",
            "GET",
            None,
        )

    @router.delete(
        "/voiceprints/enrollments/{enrollment_id}",
        response_model=EnrollmentView,
    )
    async def cancel(
        enrollment_id: str,
        authorization: str | None = Header(default=None),
    ):
        return await invoke(
            authorization,
            f"/enrollments/{quote(enrollment_id, safe='')}",
            "DELETE",
            None,
        )

    @router.put(
        "/voiceprints/enrollments/{enrollment_id}/samples/{sample_id}",
        response_model=EnrollmentView,
    )
    async def sample(
        enrollment_id: str,
        sample_id: str,
        payload: VoiceAudioRequest,
        authorization: str | None = Header(default=None),
    ):
        return await invoke(
            authorization,
            f"/enrollments/{quote(enrollment_id, safe='')}/samples/{quote(sample_id, safe='')}",
            "PUT",
            payload.model_dump(mode="json"),
        )

    @router.post(
        "/voiceprints/enrollments/{enrollment_id}/complete",
        response_model=SpeakerProfileView,
    )
    async def complete(
        enrollment_id: str,
        payload: VoiceAudioRequest,
        authorization: str | None = Header(default=None),
    ):
        return await invoke(
            authorization,
            f"/enrollments/{quote(enrollment_id, safe='')}/complete",
            "POST",
            payload.model_dump(mode="json"),
        )

    @router.post("/voiceprints/captures", response_model=VoiceCaptureSession)
    async def start_capture(response: Response, authorization: str | None = Header(default=None)):
        response.headers["Cache-Control"] = "no-store"
        return await invoke(authorization, "/captures", "POST", None)

    @router.post("/voiceprints/captures/{capture_id}/finish", response_model=VoiceAudioRequest)
    async def finish_capture(capture_id: str, response: Response,
                             authorization: str | None = Header(default=None)):
        response.headers["Cache-Control"] = "no-store"
        return await invoke(authorization, "/captures/" + quote(capture_id, safe="") + "/finish", "POST", None)

    @router.delete("/voiceprints/captures/{capture_id}")
    async def cancel_capture(capture_id: str, response: Response,
                             authorization: str | None = Header(default=None)):
        response.headers["Cache-Control"] = "no-store"
        return await invoke(authorization, "/captures/" + quote(capture_id, safe=""), "DELETE", None)

    @router.post(
        "/voiceprints/channel-calibration/samples",
        response_model=ChannelCalibrationSample,
    )
    async def channel_calibration_sample(
        payload: ChannelCalibrationUpload,
        authorization: str | None = Header(default=None),
    ):
        return await invoke(
            authorization,
            "/channel-calibration/samples",
            "POST",
            payload.model_dump(mode="json"),
        )

    @router.get(
        "/voiceprints/channel-calibration",
        response_model=ChannelCalibrationCollection,
    )
    async def channel_calibration(authorization: str | None = Header(default=None)):
        return await invoke(authorization, "/channel-calibration", "GET", None)

    @router.delete(
        "/voiceprints/channel-calibration/batches/{batch_id}",
        response_model=ChannelCalibrationCollection,
    )
    async def delete_channel_calibration_batch(
        batch_id: str,
        authorization: str | None = Header(default=None),
    ):
        return await invoke(
            authorization,
            f"/channel-calibration/batches/{quote(batch_id, safe='')}",
            "DELETE",
            None,
        )
