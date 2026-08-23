"""Strict one-operation wire contract for Local API -> Lifecycle Workflow."""

from __future__ import annotations

import asyncio
import json
import struct
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid5

from eidolon_sdk.device_foundation.v1.lifecycle import OwnerAuthorizationContext
from pydantic import BaseModel, ConfigDict, Field

from ..app.control_plane.contracts import (
    ControllerDeviceRemovalRequest,
    DeviceRef,
    DeviceRemovalResult,
)

MAX_FRAME_BYTES = 256 * 1024
_HEADER = struct.Struct("!I")
_INTENT_NAMESPACE = UUID("2c33cc48-d0ac-5f51-a685-b3d299aeb5cd")


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def removal_intent_id(*, ingress_request_id: str, owner_domain_id: str) -> str:
    value = uuid5(
        _INTENT_NAMESPACE,
        "eidolon-removal-intent-v1:"
        f"{owner_domain_id}:{ingress_request_id}",
    )
    return f"removal-intent-{value.hex}"


class RemovalOwnerAuthorizationContext(_StrictModel):
    """Local-issued domain delegation carried over an authenticated transport.

    This is deliberately separate from the kernel-authenticated peer identity.
    The peer proves which workload delivered the context; it never becomes the
    Controller actor or Owner authority named by the context.
    """

    contract_version: Literal["1"] = "1"
    issuer_workload_principal_id: Literal["eidolon-local-api"] = (
        "eidolon-local-api"
    )
    presenter_workload_principal_id: Literal["eidolon-lifecycle-workflow"] = (
        "eidolon-lifecycle-workflow"
    )
    controller_grant_generation: int = Field(ge=0)
    reset_epoch: int = Field(ge=0)
    owner_authorization_context: OwnerAuthorizationContext

    @property
    def authorized_owner_domain_id(self) -> str:
        return self.owner_authorization_context.authorized_owner_domain_id

    @property
    def actor_controller_id(self) -> str:
        return self.owner_authorization_context.actor.principal_id

    @property
    def intent_id(self) -> str:
        return self.owner_authorization_context.intent_id

    @property
    def target_device_ref(self) -> DeviceRef:
        return self.owner_authorization_context.target_device_ref

    @property
    def deadline(self) -> datetime:
        return self.owner_authorization_context.expires_at


class LifecycleRemovalCall(_StrictModel):
    """Removal delegation issued after Local revalidates the Controller."""

    operation: Literal["lifecycle.removal.create-or-resume"] = (
        "lifecycle.removal.create-or-resume"
    )
    contract_version: Literal["1"] = "1"
    payload: ControllerDeviceRemovalRequest
    authorization_context: RemovalOwnerAuthorizationContext


class LifecycleWorkflowProblem(_StrictModel):
    code: Literal[
        "AUTHN_INVALID",
        "AUTHZ_DENIED",
        "INVALID_REQUEST",
        "WORKFLOW_UNAVAILABLE",
        "WORKFLOW_FAILURE",
    ]
    detail: str = Field(min_length=1, max_length=512)
    status_code: int = Field(ge=400, le=599)


class LifecycleWorkflowReply(_StrictModel):
    operation: Literal["lifecycle.workflow-reply"] = "lifecycle.workflow-reply"
    contract_version: Literal["1"] = "1"
    result: DeviceRemovalResult | None = None
    problem: LifecycleWorkflowProblem | None = None

    def model_post_init(self, __context: object) -> None:
        if (self.result is None) == (self.problem is None):
            raise ValueError("workflow reply must contain exactly one result or problem")


def encode_frame(model: BaseModel) -> bytes:
    body = model.model_dump_json(exclude_none=True).encode("utf-8")
    if not body or len(body) > MAX_FRAME_BYTES:
        raise ValueError("workflow frame size is invalid")
    return _HEADER.pack(len(body)) + body


async def read_frame(reader: asyncio.StreamReader) -> dict[str, object]:
    header = await reader.readexactly(_HEADER.size)
    (length,) = _HEADER.unpack(header)
    if not 0 < length <= MAX_FRAME_BYTES:
        raise ValueError("workflow frame size is invalid")
    body = await reader.readexactly(length)
    try:
        value = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("workflow frame is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("workflow frame must contain an object")
    return value


async def write_frame(writer: asyncio.StreamWriter, model: BaseModel) -> None:
    writer.write(encode_frame(model))
    await writer.drain()
