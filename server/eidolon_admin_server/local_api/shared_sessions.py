"""Owner-controlled shared device visits through the existing Local API session."""

from fastapi import APIRouter, Header
from pydantic import BaseModel, ConfigDict, Field, model_validator
from eidolon_sdk.device_foundation.v1 import DeviceInstanceId
from .management.router import ManagementBackendError, _refused


class SharedStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")
    device_ids: list[DeviceInstanceId] = Field(min_length=2, max_length=16)
    input_device_id: DeviceInstanceId

    @model_validator(mode="after")
    def selected_input(self):
        if (
            len(set(self.device_ids)) != len(self.device_ids)
            or self.input_device_id not in self.device_ids
        ):
            raise ValueError("unique devices and selected input required")
        return self


class SharedClose(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")


def register_shared_session_routes(app, *, authenticate, devices):
    router = APIRouter(prefix="/api/management/v1/shared-sessions", tags=["management"])

    @router.post("/open")
    async def start(
        payload: SharedStart, authorization: str | None = Header(default=None)
    ):
        session = await authenticate(authorization)
        try:
            return await devices.open_shared_session(session=session, payload=payload)
        except ManagementBackendError as exc:
            raise _refused(exc) from exc

    @router.post("/close")
    async def close(
        payload: SharedClose, authorization: str | None = Header(default=None)
    ):
        session = await authenticate(authorization)
        try:
            return await devices.close_shared_session(
                session=session, session_id=payload.session_id
            )
        except ManagementBackendError as exc:
            raise _refused(exc) from exc

    app.include_router(router)
