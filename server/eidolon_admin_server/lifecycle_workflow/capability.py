"""Peer-authenticated, removal-only Hub capability broker.

The Workflow never receives Hub's generic management signing key.  The Admin
process already owns that key for unrelated approval operations and exposes
only these two generation-bound removal operations over this UDS.

There used to be a third, a Claim read, because the workflow re-derived its
target before revoking it. It decided nothing Hub does not decide again on the
revoke, and it could refuse an Owner's removal on its own — so the capability
went with the read.
"""

from __future__ import annotations

import asyncio
import socket
from contextlib import suppress
from pathlib import Path
from typing import Annotated, Literal, Union

from eidolon_sdk.device_foundation.v1 import BusinessOwnerId, DeviceLocalEraseOperationStatus
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from ..app.control_plane.contracts import (
    DeviceRef,
    HubClaimRevocationResult,
)
from ..app.control_plane.errors import AuthorityFailure
from .daemon import _bind_socket, _unlink_owned_socket
from .peercred import ExactUidWorkloadAuthorizer, LinuxSoPeerCredentialAdapter
from .protocol import read_frame, write_frame


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RemovalCapabilityReady(_StrictModel):
    operation: Literal["hub.removal-capability.ready"] = "hub.removal-capability.ready"


class RevokeRemovalTarget(_StrictModel):
    operation: Literal["hub.removal-target.revoke"] = "hub.removal-target.revoke"
    controller_id: str = Field(pattern=r"^ectrl-[0-9a-f]{20}$")
    intent_id: str = Field(pattern=r"^removal-intent-[0-9a-f]{32}$")
    device_ref: DeviceRef
    # Who the Owner is, distinct from which Owner Domain this Host serves. The
    # credential Hub's Admission authorizer expects carries both.
    business_owner_id: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=128)
    command_id: str = Field(min_length=1, max_length=128)


class ObserveRemovalDelivery(_StrictModel):
    operation: Literal["hub.removal-delivery.observe"] = "hub.removal-delivery.observe"
    controller_id: str = Field(pattern=r"^ectrl-[0-9a-f]{20}$")
    intent_id: str = Field(pattern=r"^removal-intent-[0-9a-f]{32}$")
    device_ref: DeviceRef
    source_claim_event_id: str = Field(min_length=1, max_length=255)


RemovalCapabilityCall = Annotated[
    Union[
        RemovalCapabilityReady,
        RevokeRemovalTarget,
        ObserveRemovalDelivery,
    ],
    Field(discriminator="operation"),
]
_CALL_ADAPTER = TypeAdapter(RemovalCapabilityCall)


class RemovalCapabilityProblem(_StrictModel):
    kind: str = Field(min_length=1, max_length=64)
    detail: str = Field(min_length=1, max_length=512)
    status_code: int = Field(ge=400, le=599)


class RemovalCapabilityReply(_StrictModel):
    operation: Literal["hub.removal-capability-reply"] = "hub.removal-capability-reply"
    ready: Literal[True] | None = None
    revocation: HubClaimRevocationResult | None = None
    delivery: DeviceLocalEraseOperationStatus | None = None
    problem: RemovalCapabilityProblem | None = None

    def model_post_init(self, __context: object) -> None:
        values = (self.ready, self.revocation, self.delivery, self.problem)
        if sum(value is not None for value in values) != 1:
            raise ValueError("capability reply must contain exactly one result")


class RemovalCapabilityBroker:
    def __init__(
        self, *, socket_path: Path, allowed_workflow_uid: int, service
    ) -> None:
        self._path = socket_path
        self._service = service
        self._reader = LinuxSoPeerCredentialAdapter()
        self._authorizer = ExactUidWorkloadAuthorizer(
            expected_uid=allowed_workflow_uid,
            principal_id="eidolon-lifecycle-workflow",
        )
        self._server: asyncio.AbstractServer | None = None
        self._listener: socket.socket | None = None
        self._identity: tuple[int, int] | None = None

    async def start(self) -> None:
        listener = _bind_socket(self._path)
        state = self._path.stat()
        identity = (state.st_dev, state.st_ino)
        try:
            self._server = await asyncio.start_unix_server(
                self._handle, sock=listener, start_serving=True
            )
        except Exception:
            listener.close()
            _unlink_owned_socket(self._path, expected_identity=identity)
            raise
        self._listener = listener
        self._identity = identity

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        if self._listener is not None:
            self._listener.close()
        _unlink_owned_socket(self._path, expected_identity=self._identity)

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            connection = writer.get_extra_info("socket")
            if connection is None:
                raise PermissionError("accepted socket unavailable")
            principal = self._authorizer.authorize(self._reader.read(connection))
            if principal.principal_id != "eidolon-lifecycle-workflow":
                raise PermissionError("workflow principal is not authorized")
            call = _CALL_ADAPTER.validate_python(
                await asyncio.wait_for(read_frame(reader), timeout=5)
            )
            reply = await self._dispatch(call)
        except (PermissionError, OSError, RuntimeError):
            reply = RemovalCapabilityReply(
                problem=RemovalCapabilityProblem(
                    kind="unauthorized",
                    detail="Lifecycle Workflow authentication failed",
                    status_code=401,
                )
            )
        except (ValidationError, ValueError, asyncio.IncompleteReadError, TimeoutError):
            reply = RemovalCapabilityReply(
                problem=RemovalCapabilityProblem(
                    kind="invalid_request",
                    detail="Removal capability request is invalid",
                    status_code=422,
                )
            )
        except AuthorityFailure as exc:
            reply = RemovalCapabilityReply(
                problem=RemovalCapabilityProblem(
                    kind=exc.kind,
                    detail=str(exc),
                    status_code=exc.status_code,
                )
            )
        except Exception:
            reply = RemovalCapabilityReply(
                problem=RemovalCapabilityProblem(
                    kind="unavailable",
                    detail="Removal capability broker is unavailable",
                    status_code=503,
                )
            )
        with suppress(Exception):
            await write_frame(writer, reply)
        writer.close()
        with suppress(BrokenPipeError, ConnectionError, OSError):
            await writer.wait_closed()

    async def _dispatch(self, call: RemovalCapabilityCall) -> RemovalCapabilityReply:
        if isinstance(call, RemovalCapabilityReady):
            return RemovalCapabilityReply(ready=True)
        credentials = self._service.hub_credentials
        if credentials is None:
            raise AuthorityFailure(
                "hub", "configuration", "Hub issuer unavailable", 503
            )
        authorization = credentials.issue_removal_intent(
            controller_id=call.controller_id,
            intent_id=call.intent_id,
            device_ref=call.device_ref,
            business_owner_id=BusinessOwnerId(call.business_owner_id),
        )
        if isinstance(call, RevokeRemovalTarget):
            return RemovalCapabilityReply(
                revocation=await self._service.hub.revoke(
                    device_ref=call.device_ref,
                    reason=call.reason,
                    command_id=call.command_id,
                    correlation_id=call.intent_id,
                    authorization=authorization,
                )
            )
        return RemovalCapabilityReply(
            delivery=await self._service.hub.get_device_control_operation(
                device_ref=call.device_ref,
                source_claim_event_id=call.source_claim_event_id,
                authorization=authorization,
            )
        )


class BrokeredRemovalHubClient:
    """Hub adapter whose only authority is the broker's fixed protocol."""

    def __init__(self, *, socket_path: Path, timeout_seconds: float) -> None:
        self._path = socket_path
        self._timeout = timeout_seconds

    async def ready(self) -> None:
        reply = await self._exchange(RemovalCapabilityReady())
        if reply.ready is not True:
            raise AuthorityFailure(
                "hub", "unavailable", "Removal capability broker is not ready", 503
            )

    async def revoke(
        self, *, device_ref, reason, command_id, correlation_id, authorization
    ) -> HubClaimRevocationResult:
        controller_id, _intent, business_owner_id = _broker_marker(authorization)
        reply = await self._exchange(
            RevokeRemovalTarget(
                controller_id=controller_id,
                intent_id=correlation_id,
                device_ref=device_ref,
                business_owner_id=business_owner_id,
                reason=reason,
                command_id=command_id,
            )
        )
        assert reply.revocation is not None
        return reply.revocation

    async def get_device_control_operation(
        self, *, device_ref, source_claim_event_id, authorization
    ) -> DeviceLocalEraseOperationStatus:
        controller_id, intent_id, _owner = _broker_marker(authorization)
        reply = await self._exchange(
            ObserveRemovalDelivery(
                controller_id=controller_id,
                intent_id=intent_id,
                device_ref=device_ref,
                source_claim_event_id=source_claim_event_id,
            )
        )
        assert reply.delivery is not None
        return reply.delivery

    async def _exchange(self, call: BaseModel) -> RemovalCapabilityReply:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(str(self._path)), timeout=self._timeout
            )
            try:
                await write_frame(writer, call)
                reply = RemovalCapabilityReply.model_validate(
                    await asyncio.wait_for(read_frame(reader), timeout=self._timeout)
                )
            finally:
                writer.close()
                with suppress(BrokenPipeError, ConnectionError, OSError):
                    await writer.wait_closed()
        except (OSError, TimeoutError, ValueError, asyncio.IncompleteReadError) as exc:
            raise AuthorityFailure(
                "hub", "unavailable", "Removal capability broker is unavailable", 503
            ) from exc
        if reply.problem is not None:
            raise AuthorityFailure(
                "hub",
                reply.problem.kind,
                reply.problem.detail,
                reply.problem.status_code,
            )
        return reply


class BrokerMarkerIssuer:
    """Non-credential correlation marker consumed only by the broker adapter."""

    def issue_removal_intent(
        self, *, controller_id, intent_id, business_owner_id, **_kwargs
    ) -> str:
        return f"broker:{controller_id}:{intent_id}:{business_owner_id}"


def _broker_marker(marker: str) -> tuple[str, str, str]:
    """The one parse of a broker marker.

    There were two, differing only in how strict they were about the shape, and
    only one marker shape is issued — which is how one spelling of a value
    becomes two rules about it.

    It carries the business Owner as well as the Controller because the
    credential the broker mints on the other side needs both, and the Workflow
    is the only side that knows them.
    """

    parts = marker.split(":")
    if len(parts) != 4 or parts[0] != "broker":
        raise AuthorityFailure("hub", "configuration", "Broker marker is invalid", 503)
    return parts[1], parts[2], parts[3]
