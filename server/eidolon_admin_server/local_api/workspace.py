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

#: What a person is told when this Host's two halves disagree about its Owner.
#:
#: Every other refusal on this contract describes something about the request.
#: This one describes the Host, names both halves, says plainly that no number
#: of retries and no phone changes it, and says which repair costs what — a
#: person holding a phone in front of a Host that will never finish setup has
#: no other way to learn any of that. It also says what a repair does *not*
#: take, because the fear that stops someone running it is that the Host has
#: to be claimed and joined to Wi-Fi all over again.
ORPHANED_OWNER_BINDING_REASON = (
    "这台主机记着一位 Owner，但它的数据面里没有对应的 Workspace，两边对不上。"
    "手机这边再读多少次都是同一个答案，换一台手机也一样，也不能替它新建一个。"
    "要在主机上修：恢复那份数据，或者运行 owner-reset 让主机忘掉这位 Owner 后重新设置一次。"
    "主机认领和 Wi-Fi 都不会回滚。"
)

def changed_setup_input_reason(owner_display_name: str | None = None) -> str:
    """Why the input was refused, and — when the Host knows it — what to type.

    A person only reaches this by being shown a setup form on a Host that
    already has a Workspace, which happens when Bootstrap has no record of one
    and Data does. Refusing them without naming the Owner the Workspace is
    under leaves them guessing a string they were never shown, on a screen
    whose only other control is the one that just failed. The Host has the
    name, and whoever is holding the phone already holds this Host.
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
    bound_owner_id: str | None,
) -> WorkspaceOperation | None:
    """This Host's Data Workspace, judged against the Owner Bootstrap holds.

    One judgement, because there is one question and both verbs of the setup
    contract ask it. Written twice, they answered it differently: reading gave
    the raw 404 from Data, writing turned the same 404 into a 503 — so a Host
    whose halves disagreed refused a phone twice over, with two status codes,
    neither of which said what was wrong. The phone only ever reached the first.

    ``owner_id`` is Host state rather than per-Controller state, and it is a
    pure function of the Host id (``owner_<uuid5(host_id).hex>``). It therefore
    says nothing about *who* the Owner is; it says only that the Data plane
    once held a Workspace for them. When that turns out to be false, it is
    false identically for every phone ever claimed onto this Host, and nothing
    a phone can send makes it true — so the refusal has to name the condition
    and where it is repaired, rather than describe the request that hit it.
    """

    try:
        existing = await workspace.get(operation_id)
    except WorkspaceSetupError as exc:
        if exc.status_code != 404:
            raise
        if bound_owner_id is not None:
            raise WorkspaceSetupError(
                "Host Owner binding has no Data workspace operation",
                status_code=409,
                reason=ORPHANED_OWNER_BINDING_REASON,
            ) from exc
        return None
    if bound_owner_id is not None and existing.owner.owner_id != bound_owner_id:
        raise WorkspaceSetupError(
            "Host Owner scope does not match its Data workspace",
            status_code=409,
            reason=(
                "这台主机记着的 Owner 和它数据面里的 Workspace 不是同一位。"
                "手机这边改不了，要在主机上修好。"
            ),
        )
    return existing


async def resolve_workspace_setup(
    workspace: AdminWorkspacePort,
    *,
    operation_id: str,
    payload: WorkspaceInitializeRequest,
    bound_owner_id: str | None,
) -> WorkspaceOperation:
    """Resume a Host-scoped operation before attempting first initialization."""

    existing = await existing_workspace(
        workspace,
        operation_id=operation_id,
        bound_owner_id=bound_owner_id,
    )
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
