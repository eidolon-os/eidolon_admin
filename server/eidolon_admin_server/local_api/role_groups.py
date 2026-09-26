"""Owner UI selects device IDs; the management boundary resolves current DeviceRefs."""
from fastapi import APIRouter, Header
from pydantic import BaseModel, ConfigDict, Field, model_validator
from eidolon_sdk.device_foundation.v1 import DeviceInstanceId
from eidolon_sdk.biz.control.coordination import RoleGroupStatus, SceneRole
from .shared_sessions import SharedClose
from .management.router import ManagementBackendError, _refused

class RoleGroupAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    output_device_id: DeviceInstanceId
    role: SceneRole


class RoleGroupStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")
    input_device_id: DeviceInstanceId
    output_device_ids: list[DeviceInstanceId] = Field(min_length=1, max_length=16)
    discussion: bool = Field(default=False, strict=True)
    reply_budget: int = Field(default=8, ge=1, le=32, strict=True)
    roles: list[RoleGroupAssignment] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def distinct(self):
        ids = [self.input_device_id, *self.output_device_ids]
        if len(set(ids)) != len(ids):
            raise ValueError("distinct input and output devices required")
        assigned = [item.output_device_id for item in self.roles]
        if len(set(assigned)) != len(assigned) or not set(assigned) <= set(self.output_device_ids):
            raise ValueError("roles require unique selected output devices")
        return self


def register_role_group_routes(app, *, authenticate, devices):
    router = APIRouter(prefix="/api/management/v1/role-groups", tags=["management"])

    async def invoke(authorization, action, payload):
        session = await authenticate(authorization)
        try:
            return await devices.role_group(session=session, action=action, payload=payload)
        except ManagementBackendError as exc:
            raise _refused(exc) from exc

    @router.post("/open", response_model=RoleGroupStatus)
    async def start(payload: RoleGroupStart, authorization: str | None = Header(default=None)):
        return await invoke(authorization, "open", payload)

    @router.post("/status", response_model=RoleGroupStatus)
    async def status(payload: SharedClose, authorization: str | None = Header(default=None)):
        return await invoke(authorization, "status", payload)

    @router.post("/close", response_model=RoleGroupStatus)
    async def close(payload: SharedClose, authorization: str | None = Header(default=None)):
        return await invoke(authorization, "close", payload)

    app.include_router(router)
