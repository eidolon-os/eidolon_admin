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


_HOST_WORKSPACE_NAMESPACE = UUID("bb5f68d3-192f-55b8-86d4-235887c426e8")


class WorkspaceSetupError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 503) -> None:
        super().__init__(message)
        self.status_code = status_code


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
            )
        if response.status_code == 422:
            raise WorkspaceSetupError(
                "Workspace setup input was rejected",
                status_code=422,
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
