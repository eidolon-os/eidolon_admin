"""Strict consumer of eidolond's service management contract.

Host service control is the same operation on both Hosts: eidolond owns the
desired state and drives supervisord on macOS or systemd on the Pi. Admin only
carries the operator's intent through, and never re-reads state on the
operator's behalf — eidolond's mutations are compare-and-swap, and resolving a
stale revision here would defeat that.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from urllib.parse import quote, urlparse

import httpx
from pydantic import ValidationError
from eidolon_sdk.system.v1 import HostVitalsWire

from .contracts import (
    HostService,
    HostServiceMutationResult,
    HostServicePage,
    MutationOperation,
)
from .errors import HostServiceError

_OPERATIONS: dict[MutationOperation, str] = {
    "restart": "system.service.restart",
    "enable": "system.service.enable",
    "disable": "system.service.disable",
}


class HostServiceClient:
    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float,
        uds_path: Path | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("System service base URL must be HTTP(S)")
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._owns_client = client is None
        if client is not None:
            self._client = client
        else:
            transport = (
                httpx.AsyncHTTPTransport(uds=str(uds_path)) if uds_path else None
            )
            self._client = httpx.AsyncClient(transport=transport, trust_env=False)

    async def read_vitals(self) -> HostVitalsWire:
        document = await self._request("GET", "/api/system/v1/vitals")
        try:
            return HostVitalsWire.model_validate(document)
        except ValidationError as exc:
            raise self._invalid("host vitals did not match the expected shape") from exc

    async def list_services(self) -> HostServicePage:
        document = await self._request("GET", "/api/system/v1/services")
        services = document.get("services")
        if not isinstance(services, list):
            raise self._invalid("service page did not carry a service list")
        try:
            return HostServicePage(
                driver=await self._host_driver(),
                services=tuple(self._service(item) for item in services),
            )
        except ValidationError as exc:
            raise self._invalid("service page did not match the expected shape") from exc

    async def _host_driver(self) -> str:
        """Which process manager eidolond is actually driving.

        The service page does not carry it; health does. An operator needs it to
        know whether they are looking at a supervisord Mac or a systemd Pi.
        """

        document = await self._request("GET", "/health")
        driver = document.get("host_driver")
        if not isinstance(driver, str) or not driver:
            raise self._invalid("the Host system manager did not report its driver")
        return driver

    async def get_service(self, service_id: str) -> HostService:
        document = await self._request(
            "GET", f"/api/system/v1/services/{quote(service_id, safe='')}"
        )
        return self._service(document)

    async def mutate(
        self,
        *,
        service_id: str,
        operation: MutationOperation,
        expected_revision: int,
        request_id: str | None = None,
    ) -> HostServiceMutationResult:
        document = await self._request(
            "POST",
            f"/api/system/v1/services/{quote(service_id, safe='')}/{operation}",
            json={
                "operation": _OPERATIONS[operation],
                "request_id": request_id or uuid.uuid4().hex,
                "expected_revision": expected_revision,
            },
        )
        state = document.get("state")
        if not isinstance(state, dict):
            raise self._invalid("mutation result did not carry a desired state")
        try:
            return HostServiceMutationResult(
                service_id=service_id,
                operation=operation,
                enabled=bool(state["enabled"]),
                revision=int(state["revision"]),
                audit_position=int(document["audit_position"]),
                replayed=bool(document["replayed"]),
            )
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            raise self._invalid("mutation result did not match the expected shape") from exc

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    @staticmethod
    def _service(document: object) -> HostService:
        if not isinstance(document, dict):
            raise HostServiceClient._invalid("service status is not an object")
        desired = document.get("desired")
        if not isinstance(desired, dict):
            raise HostServiceClient._invalid("service status did not carry a desired state")
        try:
            return HostService(
                service_id=str(document["service_id"]),
                required=bool(document["required"]),
                enabled=bool(desired["enabled"]),
                revision=int(desired["revision"]),
                runtime_state=document["runtime_state"],
                detail=document.get("detail"),
                observed_at=document["observed_at"],
                endpoints=tuple(document.get("endpoints") or ()),
            )
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            raise HostServiceClient._invalid(
                "service status did not match the expected shape"
            ) from exc

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        try:
            response = await self._client.request(
                method, f"{self._base_url}{path}", timeout=self._timeout, **kwargs
            )
        except (
            httpx.TimeoutException,
            httpx.NetworkError,
            httpx.RemoteProtocolError,
        ) as exc:
            raise HostServiceError(
                "unavailable", "the Host system manager is unreachable"
            ) from exc
        if response.status_code == 404:
            raise HostServiceError(
                "not_found", "the Host does not manage this service"
            )
        if response.status_code == 409:
            raise HostServiceError(
                "conflict", "the service changed since it was read; refresh and retry"
            )
        if response.status_code >= 400:
            raise HostServiceError(
                "rejected",
                f"the Host system manager rejected the request ({response.status_code})",
            )
        try:
            document = response.json()
        except ValueError as exc:
            raise HostServiceClient._invalid("response was not JSON") from exc
        if not isinstance(document, dict):
            raise HostServiceClient._invalid("response was not a JSON object")
        return document

    @staticmethod
    def _invalid(message: str) -> HostServiceError:
        return HostServiceError("invalid_response", message)
