"""The conversation one of the Owner's devices holds, via the existing Owner session."""
from typing import Literal

from fastapi import APIRouter, Header
from pydantic import BaseModel, ConfigDict
from eidolon_sdk.device_foundation.v1 import DeviceInstanceId
from eidolon_sdk.biz.control.device_session import ConversationId, DeviceSessionStatus
from .management.router import ManagementBackendError, _refused

class DeviceSessionTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_id: DeviceInstanceId
    # Close only: end this conversation, not one the device started since.
    conversation_id: ConversationId | None = None


def register_device_session_routes(app, *, authenticate, devices):
    router = APIRouter(prefix="/api/management/v1/device-sessions", tags=["management"])

    @router.post("/{action}", response_model=DeviceSessionStatus)
    async def device_session(action: Literal["open", "status", "close"], payload: DeviceSessionTarget,
                             authorization: str | None = Header(default=None)):
        session = await authenticate(authorization)
        try:
            return await devices.device_session(session=session, action=action, payload=payload)
        except ManagementBackendError as exc:
            raise _refused(exc) from exc

    app.include_router(router)
