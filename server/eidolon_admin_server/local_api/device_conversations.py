"""Explicit microphone, speaker and Companion selection via the existing Owner session."""
from fastapi import APIRouter, Header
from pydantic import BaseModel, ConfigDict, Field, model_validator
from eidolon_sdk.device_foundation.v1 import DeviceInstanceId
from eidolon_sdk.biz.control.device_conversation import DeviceConversationStatus
from .shared_sessions import SharedClose
from .management.router import ManagementBackendError, _refused

class ConversationStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")
    input_device_id: DeviceInstanceId
    output_device_id: DeviceInstanceId
    target_companion_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")

    @model_validator(mode="after")
    def distinct(self):
        if self.input_device_id == self.output_device_id:
            raise ValueError("distinct input and output devices required")
        return self


def register_device_conversation_routes(app, *, authenticate, devices):
    router = APIRouter(prefix="/api/management/v1/device-conversations", tags=["management"])

    async def invoke(authorization, action, payload):
        session = await authenticate(authorization)
        try:
            return await devices.device_conversation(session=session, action=action, payload=payload)
        except ManagementBackendError as exc:
            raise _refused(exc) from exc

    @router.post("/open", response_model=DeviceConversationStatus)
    async def start(payload: ConversationStart, authorization: str | None = Header(default=None)):
        return await invoke(authorization, "open", payload)

    @router.post("/status", response_model=DeviceConversationStatus)
    async def status(payload: SharedClose, authorization: str | None = Header(default=None)):
        return await invoke(authorization, "status", payload)

    @router.post("/close", response_model=DeviceConversationStatus)
    async def close(payload: SharedClose, authorization: str | None = Header(default=None)):
        return await invoke(authorization, "close", payload)

    app.include_router(router)
