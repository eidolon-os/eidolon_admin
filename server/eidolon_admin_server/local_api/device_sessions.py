"""Explicit microphone, speaker and Companion selection via the existing Owner session."""
from fastapi import APIRouter, Header
from pydantic import BaseModel, ConfigDict, Field
from eidolon_sdk.device_foundation.v1 import DeviceInstanceId
from eidolon_sdk.biz.control.device_session import DeviceSessionStatus
from .shared_sessions import SharedClose
from .management.router import ManagementBackendError, _refused

class DeviceSessionStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")
    device_id: DeviceInstanceId
    target_companion_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")



def register_device_session_routes(app, *, authenticate, devices):
    router = APIRouter(prefix="/api/management/v1/device-sessions", tags=["management"])

    async def invoke(authorization, action, payload):
        session = await authenticate(authorization)
        try:
            return await devices.device_session(session=session, action=action, payload=payload)
        except ManagementBackendError as exc:
            raise _refused(exc) from exc

    @router.post("/open", response_model=DeviceSessionStatus)
    async def start(payload: DeviceSessionStart, authorization: str | None = Header(default=None)):
        return await invoke(authorization, "open", payload)

    @router.post("/status", response_model=DeviceSessionStatus)
    async def status(payload: SharedClose, authorization: str | None = Header(default=None)):
        return await invoke(authorization, "status", payload)

    @router.post("/close", response_model=DeviceSessionStatus)
    async def close(payload: SharedClose, authorization: str | None = Header(default=None)):
        return await invoke(authorization, "close", payload)

    app.include_router(router)
