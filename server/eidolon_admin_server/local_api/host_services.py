"""Host service control for the Mobile product boundary.

Mobile manages the same services the Admin Web does; both go through Admin to
eidolond, which owns per-service desired state. The view is deliberately
narrower than Admin's: an Owner acts on "is it running, restart it", not on
endpoint addresses or contract ids.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

RuntimeState = Literal[
    "unknown", "inactive", "starting", "ready", "degraded", "blocked", "failed"
]
MutationOperation = Literal["restart", "enable", "disable"]


class HostServiceControlError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 503) -> None:
        super().__init__(message)
        self.status_code = status_code


class AdminHostServicesPort(Protocol):
    async def list_services(self) -> dict: ...

    async def mutate(
        self,
        *,
        service_id: str,
        operation: MutationOperation,
        expected_revision: int,
    ) -> dict: ...

    async def close(self) -> None: ...


class LocalHostServiceView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service_id: str = Field(min_length=1, max_length=128)
    required: bool
    enabled: bool
    revision: int = Field(ge=1)
    runtime_state: RuntimeState
    detail: str | None = None
    observed_at: datetime


class LocalHostServiceInventoryView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    services: tuple[LocalHostServiceView, ...] = ()


class LocalHostServiceMutationView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service_id: str = Field(min_length=1, max_length=128)
    operation: MutationOperation
    enabled: bool
    revision: int = Field(ge=1)


def host_service_inventory(document: dict) -> LocalHostServiceInventoryView:
    services = document.get("services")
    if not isinstance(services, list):
        raise HostServiceControlError("Host service inventory is unreadable", status_code=502)
    try:
        return LocalHostServiceInventoryView(
            services=tuple(
                LocalHostServiceView(
                    service_id=item["service_id"],
                    required=item["required"],
                    enabled=item["enabled"],
                    revision=item["revision"],
                    runtime_state=item["runtime_state"],
                    detail=item.get("detail"),
                    observed_at=item["observed_at"],
                )
                for item in services
            )
        )
    except (KeyError, TypeError, ValidationError) as exc:
        raise HostServiceControlError(
            "Host service inventory did not match the expected shape", status_code=502
        ) from exc


def host_service_mutation(document: dict) -> LocalHostServiceMutationView:
    try:
        return LocalHostServiceMutationView(
            service_id=document["service_id"],
            operation=document["operation"],
            enabled=document["enabled"],
            revision=document["revision"],
        )
    except (KeyError, TypeError, ValidationError) as exc:
        raise HostServiceControlError(
            "Host service result did not match the expected shape", status_code=502
        ) from exc


class AdminHostServicesClient:
    def __init__(
        self,
        *,
        base_url: str,
        service_token: str,
        timeout_seconds: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = service_token
        self._timeout = timeout_seconds
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(trust_env=False)

    async def list_services(self) -> dict:
        return await self._request("GET", "/api/host/services")

    async def mutate(
        self,
        *,
        service_id: str,
        operation: MutationOperation,
        expected_revision: int,
    ) -> dict:
        return await self._request(
            "POST",
            f"/api/host/services/{quote(service_id, safe='')}/{operation}",
            json={"expected_revision": expected_revision},
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        try:
            response = await self._client.request(
                method,
                f"{self._base_url}{path}",
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=self._timeout,
                **kwargs,
            )
        except (
            httpx.TimeoutException,
            httpx.NetworkError,
            httpx.RemoteProtocolError,
        ) as exc:
            raise HostServiceControlError("Host services are unreachable") from exc
        if response.status_code == 404:
            raise HostServiceControlError(
                "the Host does not manage this service", status_code=404
            )
        if response.status_code == 409:
            raise HostServiceControlError(
                "the service changed since it was read; refresh and retry",
                status_code=409,
            )
        if response.status_code >= 400:
            raise HostServiceControlError(
                "Host service control rejected the request",
                status_code=502 if response.status_code >= 500 else response.status_code,
            )
        try:
            document = response.json()
        except ValueError as exc:
            raise HostServiceControlError(
                "Host service response was not JSON", status_code=502
            ) from exc
        if not isinstance(document, dict):
            raise HostServiceControlError(
                "Host service response was not a JSON object", status_code=502
            )
        return document
