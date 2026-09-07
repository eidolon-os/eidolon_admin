"""Exact Local API adapter for Admin's loopback workspace contract."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID, uuid5

import httpx
from pydantic import BaseModel, ConfigDict, Field

from ..app.control_plane.contracts import (
    WorkspaceInitializeRequest,
    WorkspaceOperation,
)
from ..app.control_plane.workspace_policy import workspace_request_fingerprint


_HOST_WORKSPACE_NAMESPACE = UUID("bb5f68d3-192f-55b8-86d4-235887c426e8")


def changed_setup_input_reason(owner_display_name: str | None = None) -> str:
    """Why the input was refused, and — when the Host knows it — what to type.

    A person reaches this by being shown a setup form for a Workspace that
    already exists, which happens when their phone has not read this Host
    before. Refusing without naming the Owner the Workspace is under leaves
    them guessing a string they were never shown, on a screen whose only other
    control is the one that just failed. The Host has the name, and whoever is
    holding the phone already holds this Host.
    """

    if owner_display_name is None:
        return "这台主机的 Workspace 已经用另一份设置信息创建过了，不能用新的名字覆盖它。"
    return (
        f"这台主机上已经有一个 Workspace，Owner 的名字是「{owner_display_name}」。"
        "填这个名字就能接着用它；想换一个名字，要先在主机上重置。"
    )


class WorkspaceSetupError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int = 503,
        reason: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.reason = reason


def workspace_setup_detail(exc: WorkspaceSetupError) -> str | dict[str, str]:
    """The HTTP body for this refusal, in the shape the App already reads.

    The same split ``device_admission_detail`` draws, for the same reason: a
    bare string detail is operator prose naming authorities and contracts, and
    the App is right to drop it. Only a tagged ``{"reason": ...}`` reaches a
    person — so a Host that knows exactly why it refused has to say so in this
    shape or not be heard at all. Every refusal below that a person could act
    on, or would otherwise sit in front of forever, carries one.
    """

    return {"reason": exc.reason} if exc.reason is not None else str(exc)


class WorkspaceSetupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner_display_name: str = Field(min_length=1, max_length=128)
    companion_display_name: str = Field(default="Eidolon", min_length=1, max_length=128)

    def to_admin(self) -> WorkspaceInitializeRequest:
        return WorkspaceInitializeRequest.model_validate(self.model_dump())


class AdminWorkspacePort(Protocol):
    async def initialize(
        self,
        *,
        operation_id: str,
        payload: WorkspaceInitializeRequest,
    ) -> WorkspaceOperation: ...

    async def get(self, operation_id: str) -> WorkspaceOperation: ...

    async def close(self) -> None: ...


async def existing_workspace(
    workspace: AdminWorkspacePort,
    *,
    operation_id: str,
) -> WorkspaceOperation | None:
    """This Host's Data Workspace, or nothing if the Data plane has none.

    One judgement, because there is one question and everything that needs an
    Owner asks it: both setup verbs, the unauthenticated readiness answer, and
    a session resolving its scope. Written more than once it was answered more
    than one way — reading passed Data's 404 straight through, writing turned
    the same 404 into a 503, and a phone only ever reached the first.

    There is deliberately no second opinion to reconcile against. This used to
    take the Owner Bootstrap held and refuse when the two disagreed, which is
    the only reason "disagree" was a state at all: ``owner_id`` was
    ``owner_<uuid5(host_id).hex>``, derivable from the Host id, so Bootstrap's
    copy added no knowledge — only the chance of being wrong, durably, in the
    direction no phone could repair. One holder, nothing to reconcile.
    """

    try:
        return await workspace.get(operation_id)
    except WorkspaceSetupError as exc:
        if exc.status_code != 404:
            raise
        return None


async def resolve_workspace_setup(
    workspace: AdminWorkspacePort,
    *,
    operation_id: str,
    payload: WorkspaceInitializeRequest,
) -> WorkspaceOperation:
    """Resume a Host-scoped operation before attempting first initialization."""

    existing = await existing_workspace(workspace, operation_id=operation_id)
    if existing is None:
        return await workspace.initialize(operation_id=operation_id, payload=payload)
    if existing.request_fingerprint != workspace_request_fingerprint(payload):
        raise WorkspaceSetupError(
            "This Host workspace was initialized with different setup input",
            status_code=409,
            reason=changed_setup_input_reason(existing.owner.display_name),
        )
    return existing


class AdminWorkspaceClient:
    def __init__(
        self,
        *,
        base_url: str,
        service_token: str,
        timeout_seconds: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = service_token.strip()
        self._timeout = timeout_seconds
        self._client = client or httpx.AsyncClient(trust_env=False)
        self._owns_client = client is None

    def _headers(self) -> dict[str, str]:
        if not self._token:
            raise WorkspaceSetupError(
                "Local API Admin service credential is not configured"
            )
        return {"Authorization": f"Bearer {self._token}"}

    def _url(self, operation_id: str) -> str:
        return (
            f"{self._base_url}/api/control-plane/v1/"
            f"workspace-onboarding/operations/{operation_id}"
        )

    async def initialize(
        self,
        *,
        operation_id: str,
        payload: WorkspaceInitializeRequest,
    ) -> WorkspaceOperation:
        return await self._request(
            "PUT",
            operation_id,
            json=payload.model_dump(mode="json"),
        )

    async def get(self, operation_id: str) -> WorkspaceOperation:
        return await self._request("GET", operation_id)

    async def _request(
        self,
        method: str,
        operation_id: str,
        *,
        json: dict | None = None,
    ) -> WorkspaceOperation:
        try:
            response = await self._client.request(
                method,
                self._url(operation_id),
                headers=self._headers(),
                json=json,
                timeout=self._timeout,
            )
        except (
            httpx.TimeoutException,
            httpx.NetworkError,
            httpx.RemoteProtocolError,
        ) as exc:
            raise WorkspaceSetupError(
                "Admin workspace control plane is unavailable"
            ) from exc
        if response.status_code == 409:
            raise WorkspaceSetupError(
                "This Host workspace was initialized with different setup input",
                status_code=409,
                reason=changed_setup_input_reason(),
            )
        if response.status_code == 422:
            raise WorkspaceSetupError(
                "Workspace setup input was rejected",
                status_code=422,
                reason="Workspace 名称未被主机接受，请检查后重试。",
            )
        if response.status_code == 404:
            raise WorkspaceSetupError(
                "Workspace operation does not exist",
                status_code=404,
            )
        if response.status_code != 200:
            raise WorkspaceSetupError("Admin workspace control plane is unavailable")
        try:
            result = WorkspaceOperation.model_validate(response.json())
        except (ValueError, TypeError) as exc:
            raise WorkspaceSetupError(
                "Admin workspace response violated its contract"
            ) from exc
        if result.operation_id != operation_id:
            raise WorkspaceSetupError(
                "Admin workspace response returned another operation"
            )
        return result

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def host_workspace_operation_id(host_id: str) -> str:
    if not isinstance(host_id, str) or not host_id.startswith("ehost-"):
        raise ValueError("Host ID is invalid for workspace setup")
    return str(uuid5(_HOST_WORKSPACE_NAMESPACE, f"eidolon-host-workspace-v1:{host_id}"))


def workspace_status(
    *,
    operation_id: str,
    result: WorkspaceOperation | None,
) -> dict:
    if result is None:
        return {
            "contract_version": "1",
            "operation_id": operation_id,
            "state": "absent",
            "owner": None,
            "workspace": None,
        }
    return {
        "contract_version": "1",
        "operation_id": operation_id,
        "state": "ready",
        "owner": result.owner.model_dump(mode="json"),
        "workspace": result.workspace.model_dump(mode="json"),
    }
