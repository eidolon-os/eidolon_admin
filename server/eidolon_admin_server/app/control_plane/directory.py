"""Strict client for eidolond's public endpoint directory."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote, urlparse

import httpx
from pydantic import ValidationError

from .contracts import ServiceEndpoint
from .errors import AuthorityFailure


class SystemDirectoryClient:
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
            raise ValueError("System Directory base URL must be HTTP(S)")
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

    async def resolve(
        self,
        *,
        service_id: str,
        endpoint_id: str,
        required_contract: str,
    ) -> ServiceEndpoint:
        url = (
            f"{self._base_url}/api/system/v1/services/{quote(service_id, safe='')}"
            f"/endpoints/{quote(endpoint_id, safe='')}"
        )
        try:
            response = await self._client.get(url, timeout=self._timeout)
        except (
            httpx.TimeoutException,
            httpx.NetworkError,
            httpx.RemoteProtocolError,
        ) as exc:
            raise AuthorityFailure(
                "directory",
                "unavailable",
                "System Service Directory is unreachable",
                503,
                retryable=True,
            ) from exc
        if response.status_code in {404, 503}:
            raise AuthorityFailure(
                "directory",
                "unavailable",
                f"service endpoint is not ready: {service_id}/{endpoint_id}",
                503,
                upstream_status=response.status_code,
                retryable=True,
            )
        if response.status_code != 200:
            raise AuthorityFailure(
                "directory",
                "upstream_failure",
                f"unexpected System Service Directory status {response.status_code}",
                502,
                upstream_status=response.status_code,
                retryable=response.status_code >= 500,
            )
        try:
            endpoint = ServiceEndpoint.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise AuthorityFailure(
                "directory",
                "contract_violation",
                "System Service Directory response violated the consumed contract",
                502,
            ) from exc
        if endpoint.service_id != service_id or endpoint.endpoint_id != endpoint_id:
            raise AuthorityFailure(
                "directory",
                "contract_violation",
                "System Service Directory returned a different endpoint identity",
                502,
            )
        if endpoint.protocol != "http":
            raise AuthorityFailure(
                "directory",
                "contract_violation",
                "resolved endpoint is not HTTP",
                502,
            )
        address = urlparse(endpoint.address)
        if address.scheme not in {"http", "https"} or not address.netloc:
            raise AuthorityFailure(
                "directory",
                "contract_violation",
                "resolved endpoint address is not a valid HTTP URL",
                502,
            )
        if endpoint.contract != required_contract:
            raise AuthorityFailure(
                "directory",
                "contract_violation",
                f"endpoint contract mismatch for {service_id}/{endpoint_id}",
                502,
            )
        return endpoint

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
