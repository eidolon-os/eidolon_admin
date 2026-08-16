"""Transport adapters for Data, Hub and Kernel public application contracts."""

from __future__ import annotations

from typing import TypeVar
from urllib.parse import quote

import httpx
from eidolon_sdk.biz.system_data import CompanionRuntimeSnapshot
from pydantic import BaseModel, ValidationError

from .contracts import (
    PersonaChapter,
    PersonaTimeline,
    CompanionIdentity,
    HubDevicePage,
    HubLifecycleStatus,
    KernelMountPage,
    KernelMutationResult,
    WorkspaceInitializeRequest,
    WorkspaceOperation,
)
from .directory import SystemDirectoryClient
from .errors import AuthorityFailure
from .workspace_policy import workspace_request_fingerprint

DATA_CONTRACT = "https://eidolon.dev/data/contracts/v1/companion/identity.schema.json"
DATA_RUNTIME_CONTRACT = (
    "https://eidolon.dev/data/contracts/v1/companion/runtime-snapshot.schema.json"
)
DATA_WORKSPACE_CONTRACT = (
    "https://eidolon.live/contracts/system-data/workspace/"
    "onboarding-operation-v1.schema.json"
)
HUB_CONTRACT = "eidolon.hub.device-directory.v1"
KERNEL_CONTRACT = "eidolon.kernel.device-mount.v1"

ModelT = TypeVar("ModelT", bound=BaseModel)


def _contract_violation(authority: str, detail: str) -> AuthorityFailure:
    return AuthorityFailure(authority, "contract_violation", detail, 502)


def _response_detail(response: httpx.Response) -> str:
    try:
        value = response.json()
        if isinstance(value, dict) and value.get("detail"):
            return str(value["detail"])[:500]
    except ValueError:
        pass
    return f"upstream returned HTTP {response.status_code}"


def _raise_status(authority: str, response: httpx.Response) -> None:
    status = response.status_code
    detail = _response_detail(response)
    if status == 401:
        raise AuthorityFailure(authority, "unauthorized", detail, 401, status, False)
    if status == 403:
        raise AuthorityFailure(authority, "forbidden", detail, 403, status, False)
    if status == 404:
        raise AuthorityFailure(authority, "not_found", detail, 404, status, False)
    if status in {409, 412}:
        raise AuthorityFailure(authority, "conflict", detail, 409, status, False)
    if status == 400:
        raise AuthorityFailure(authority, "invalid_request", detail, 422, status, False)
    if status == 422:
        raise AuthorityFailure(authority, "invalid_request", detail, 422, status, False)
    if status >= 500:
        raise AuthorityFailure(authority, "upstream_failure", detail, 502, status, True)
    raise AuthorityFailure(authority, "upstream_failure", detail, 502, status, False)


def _parse(authority: str, response: httpx.Response, model: type[ModelT]) -> ModelT:
    if not 200 <= response.status_code < 300:
        _raise_status(authority, response)
    try:
        return model.model_validate(response.json())
    except (ValueError, ValidationError) as exc:
        raise AuthorityFailure(
            authority,
            "contract_violation",
            f"{authority} response violated the consumed contract",
            502,
        ) from exc


async def _request(
    authority: str,
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    timeout: float,
    headers: dict[str, str] | None = None,
    json: dict | None = None,
) -> httpx.Response:
    try:
        return await client.request(
            method,
            url,
            timeout=timeout,
            headers=headers,
            json=json,
        )
    except (
        httpx.TimeoutException,
        httpx.NetworkError,
        httpx.RemoteProtocolError,
    ) as exc:
        raise AuthorityFailure(
            authority,
            "unavailable",
            f"{authority} authority is unreachable",
            503,
            retryable=True,
        ) from exc


class DataAuthorityClient:
    def __init__(
        self,
        *,
        directory: SystemDirectoryClient,
        client: httpx.AsyncClient,
        service_token: str,
        timeout_seconds: float,
    ) -> None:
        self._directory = directory
        self._client = client
        self._token = service_token.strip()
        self._timeout = timeout_seconds

    async def get_companion(self, companion_id: str) -> CompanionIdentity:
        if not self._token:
            raise AuthorityFailure(
                "data",
                "configuration",
                "Admin Data authority credential is not configured",
                503,
                retryable=False,
            )
        endpoint = await self._directory.resolve(
            service_id="data",
            endpoint_id="companion-authority.http",
            required_contract=DATA_CONTRACT,
        )
        response = await _request(
            "data",
            self._client,
            "GET",
            f"{endpoint.address.rstrip('/')}/api/companion-authority/v1/companions/"
            f"{quote(companion_id, safe='')}",
            timeout=self._timeout,
            headers={"Authorization": f"Bearer {self._token}"},
        )
        identity = _parse("data", response, CompanionIdentity)
        if identity.companion_id != companion_id:
            raise _contract_violation(
                "data", "Data returned a different companion identity"
            )
        return identity

    async def rename_companion(
        self,
        companion_id: str,
        display_name: str,
    ) -> CompanionIdentity:
        """Set what this Companion is called.

        Whether the caller may rename *this* Companion is not decided here.
        This client speaks to Data on Admin's behalf; the question of whose
        Companion it is belongs where an Owner's authority is known, which is
        the Local API boundary. Deciding it twice would mean deciding it
        differently one day.
        """

        if not self._token:
            raise AuthorityFailure(
                "data",
                "configuration",
                "Admin Data authority credential is not configured",
                503,
                retryable=False,
            )
        endpoint = await self._directory.resolve(
            service_id="data",
            endpoint_id="companion-authority.http",
            required_contract=DATA_CONTRACT,
        )
        response = await _request(
            "data",
            self._client,
            "PATCH",
            f"{endpoint.address.rstrip('/')}/api/companion-authority/v1/companions/"
            f"{quote(companion_id, safe='')}",
            timeout=self._timeout,
            headers={"Authorization": f"Bearer {self._token}"},
            json={"display_name": display_name},
        )
        identity = _parse("data", response, CompanionIdentity)
        if identity.companion_id != companion_id:
            raise _contract_violation(
                "data", "Data returned a different companion identity"
            )
        return identity

    async def get_persona_timeline(self, companion_id: str) -> PersonaTimeline:
        return await self._companion_call(
            "GET",
            f"{companion_id}/persona-timeline",
            companion_id,
            PersonaTimeline,
        )

    async def restore_persona(
        self,
        companion_id: str,
        genome_id: str,
        change_summary: str,
    ) -> PersonaChapter:
        return await self._companion_call(
            "POST",
            f"{companion_id}/persona-restorations",
            companion_id,
            PersonaChapter,
            json={"genome_id": genome_id, "change_summary": change_summary},
        )

    async def _companion_call(
        self,
        method: str,
        path: str,
        companion_id: str,
        model: type,
        *,
        json: dict | None = None,
    ):
        if not self._token:
            raise AuthorityFailure(
                "data",
                "configuration",
                "Admin Data authority credential is not configured",
                503,
                retryable=False,
            )
        endpoint = await self._directory.resolve(
            service_id="data",
            endpoint_id="companion-authority.http",
            required_contract=DATA_CONTRACT,
        )
        head, _, tail = path.partition("/")
        response = await _request(
            "data",
            self._client,
            method,
            f"{endpoint.address.rstrip('/')}/api/companion-authority/v1/companions/"
            f"{quote(head, safe='')}/{tail}",
            timeout=self._timeout,
            headers={"Authorization": f"Bearer {self._token}"},
            json=json,
        )
        return _parse("data", response, model)

    async def get_owner_primary_runtime(
        self,
        owner_id: str,
    ) -> CompanionRuntimeSnapshot:
        if not self._token:
            raise AuthorityFailure(
                "data",
                "configuration",
                "Admin Data authority credential is not configured",
                503,
                retryable=False,
            )
        endpoint = await self._directory.resolve(
            service_id="data",
            endpoint_id="companion-runtime-authority.http",
            required_contract=DATA_RUNTIME_CONTRACT,
        )
        response = await _request(
            "data",
            self._client,
            "GET",
            f"{endpoint.address.rstrip('/')}/api/companion-authority/v1/owners/"
            f"{quote(owner_id, safe='')}/primary-runtime-snapshot",
            timeout=self._timeout,
            headers={"Authorization": f"Bearer {self._token}"},
        )
        runtime = _parse("data", response, CompanionRuntimeSnapshot)
        if runtime.owner_id != owner_id:
            raise _contract_violation(
                "data", "Data returned a different Owner runtime snapshot"
            )
        return runtime


class DataWorkspaceAuthorityClient:
    """Narrow write client for first Owner workspace initialization only."""

    def __init__(
        self,
        *,
        directory: SystemDirectoryClient,
        client: httpx.AsyncClient,
        service_token: str,
        timeout_seconds: float,
    ) -> None:
        self._directory = directory
        self._client = client
        self._token = service_token.strip()
        self._timeout = timeout_seconds

    async def _base_url(self) -> str:
        if not self._token:
            raise AuthorityFailure(
                "data",
                "configuration",
                "Admin Data workspace authority credential is not configured",
                503,
                retryable=False,
            )
        endpoint = await self._directory.resolve(
            service_id="data-workspace",
            endpoint_id="workspace-authority.http",
            required_contract=DATA_WORKSPACE_CONTRACT,
        )
        return endpoint.address.rstrip("/")

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    async def initialize(
        self,
        *,
        operation_id: str,
        payload: WorkspaceInitializeRequest,
    ) -> WorkspaceOperation:
        response = await _request(
            "data",
            self._client,
            "PUT",
            f"{await self._base_url()}/api/workspace-authority/v1/operations/"
            f"{quote(operation_id, safe='')}",
            timeout=self._timeout,
            headers=self._headers,
            json=payload.model_dump(mode="json"),
        )
        result = _parse("data", response, WorkspaceOperation)
        if result.operation_id != operation_id:
            raise _contract_violation(
                "data", "Data returned a different workspace operation"
            )
        if result.request_fingerprint != workspace_request_fingerprint(payload):
            raise _contract_violation(
                "data", "Data returned a workspace operation for different setup input"
            )
        return result

    async def get(self, operation_id: str) -> WorkspaceOperation:
        response = await _request(
            "data",
            self._client,
            "GET",
            f"{await self._base_url()}/api/workspace-authority/v1/operations/"
            f"{quote(operation_id, safe='')}",
            timeout=self._timeout,
            headers=self._headers,
        )
        result = _parse("data", response, WorkspaceOperation)
        if result.operation_id != operation_id:
            raise _contract_violation(
                "data", "Data returned a different workspace operation"
            )
        return result


class HubManagementClient:
    def __init__(
        self,
        *,
        directory: SystemDirectoryClient,
        client: httpx.AsyncClient,
        timeout_seconds: float,
    ) -> None:
        self._directory = directory
        self._client = client
        self._timeout = timeout_seconds

    async def _base_url(self) -> str:
        endpoint = await self._directory.resolve(
            service_id="hub",
            endpoint_id="device-authority.http",
            required_contract=HUB_CONTRACT,
        )
        return endpoint.address.rstrip("/")

    @staticmethod
    def _headers(authorization: str) -> dict[str, str]:
        if not authorization.strip():
            raise AuthorityFailure(
                "hub",
                "unauthorized",
                "Bearer Hub management credential required",
                401,
            )
        return {"Authorization": authorization}

    async def approve(
        self,
        *,
        device_id: str,
        owner_id: str,
        request_id: str,
        authorization: str,
    ) -> HubLifecycleStatus:
        base_url = await self._base_url()
        response = await _request(
            "hub",
            self._client,
            "POST",
            f"{base_url}/api/device-management/v1/devices/{quote(device_id, safe='')}/approval",
            timeout=self._timeout,
            headers=self._headers(authorization),
            json={
                "operation": "device.approval",
                "request_id": request_id,
                "owner_id": owner_id,
            },
        )
        result = _parse("hub", response, HubLifecycleStatus)
        if (
            result.device_id != device_id
            or result.owner_id != owner_id
            or result.lifecycle_state != "approved"
        ):
            raise _contract_violation(
                "hub",
                "Hub approval response did not confirm the requested owner/device",
            )
        return result

    async def revoke(
        self,
        *,
        device_id: str,
        owner_scope: str | None,
        reason: str,
        request_id: str,
        authorization: str,
    ) -> HubLifecycleStatus:
        """Withdraw a device's grant on behalf of the owner who names it.

        `owner_scope` has no default here either. The Hub refuses a revocation
        naming an owner that does not hold the device, and this call site is
        where Admin has to decide what it is claiming.
        """

        base_url = await self._base_url()
        response = await _request(
            "hub",
            self._client,
            "POST",
            f"{base_url}/api/device-management/v1/devices/{quote(device_id, safe='')}/revocation",
            timeout=self._timeout,
            headers=self._headers(authorization),
            json={
                "operation": "device.revocation",
                "request_id": request_id,
                "reason": reason,
                **({"owner_scope": owner_scope} if owner_scope else {}),
            },
        )
        result = _parse("hub", response, HubLifecycleStatus)
        if result.device_id != device_id or result.lifecycle_state != "revoked":
            raise _contract_violation(
                "hub",
                "Hub revocation response did not confirm the requested device",
            )
        return result

    async def rename(
        self,
        *,
        device_id: str,
        owner_scope: str,
        display_name: str,
        authorization: str,
    ) -> HubDevice:
        """Set what a device is called, for the Owner who holds it."""

        base_url = await self._base_url()
        response = await _request(
            "hub",
            self._client,
            "PATCH",
            f"{base_url}/api/device-management/v1/devices/{quote(device_id, safe='')}",
            timeout=self._timeout,
            headers=self._headers(authorization),
            json={
                "operation": "device.rename",
                "display_name": display_name,
                "owner_scope": owner_scope,
            },
        )
        status = _parse("hub", response, HubLifecycleStatus)
        if status.device_id != device_id:
            raise _contract_violation(
                "hub", "Hub rename response named a different device"
            )
        page = await self.list_devices(
            owner_id=owner_scope,
            authorization=authorization,
        )
        for device in page.devices:
            if device.device_id == device_id:
                return device
        raise _contract_violation(
            "hub", "Hub directory no longer holds the device it just renamed"
        )

    async def list_devices(
        self,
        *,
        owner_id: str,
        authorization: str,
        limit: int = 100,
    ) -> HubDevicePage:
        base_url = await self._base_url()
        response = await _request(
            "hub",
            self._client,
            "GET",
            f"{base_url}/api/device-management/v1/owners/{quote(owner_id, safe='')}"
            f"/devices?limit={limit}",
            timeout=self._timeout,
            headers=self._headers(authorization),
            json=None,
        )
        page = _parse("hub", response, HubDevicePage)
        if any(device.owner_scope != owner_id for device in page.devices):
            raise _contract_violation(
                "hub", "Hub directory page crossed the requested owner scope"
            )
        return page


class KernelMountClient:
    def __init__(
        self,
        *,
        directory: SystemDirectoryClient,
        client: httpx.AsyncClient,
        timeout_seconds: float,
    ) -> None:
        self._directory = directory
        self._client = client
        self._timeout = timeout_seconds

    async def _base_url(self) -> str:
        endpoint = await self._directory.resolve(
            service_id="kernel",
            endpoint_id="device-mount.http",
            required_contract=KERNEL_CONTRACT,
        )
        return endpoint.address.rstrip("/")

    @staticmethod
    def _headers(owner_id: str) -> dict[str, str]:
        return {"X-Eidolon-Owner": owner_id}

    async def mount(
        self,
        *,
        owner_id: str,
        device_id: str,
        request_id: str,
        expected_revision: int,
        replace_existing: bool,
    ) -> KernelMutationResult:
        response = await _request(
            "kernel",
            self._client,
            "POST",
            f"{await self._base_url()}/api/kernel/v1/device-mounts",
            timeout=self._timeout,
            headers=self._headers(owner_id),
            json={
                "operation": "device.mount",
                "request_id": request_id,
                "device_id": device_id,
                "expected_revision": expected_revision,
                "replace_existing": replace_existing,
            },
        )
        result = _parse("kernel", response, KernelMutationResult)
        if (
            result.mount.device_id != device_id
            or result.mount.owner_id != owner_id
            or not result.mount.active
        ):
            raise _contract_violation(
                "kernel",
                "Kernel Mount response did not confirm the requested owner/device",
            )
        return result

    async def attach(
        self,
        *,
        owner_id: str,
        device_id: str,
        companion_id: str,
        request_id: str,
        expected_revision: int,
    ) -> KernelMutationResult:
        response = await _request(
            "kernel",
            self._client,
            "POST",
            f"{await self._base_url()}/api/kernel/v1/device-mounts/devices/"
            f"{quote(device_id, safe='')}/attachment",
            timeout=self._timeout,
            headers=self._headers(owner_id),
            json={
                "operation": "companion.attach",
                "request_id": request_id,
                "companion_id": companion_id,
                "expected_revision": expected_revision,
            },
        )
        result = _parse("kernel", response, KernelMutationResult)
        if (
            result.mount.device_id != device_id
            or result.mount.owner_id != owner_id
            or result.mount.attached_companion_id != companion_id
            or not result.mount.active
        ):
            raise _contract_violation(
                "kernel",
                "Kernel Attachment response did not confirm the requested identities",
            )
        return result

    async def unmount(
        self,
        *,
        owner_id: str,
        device_id: str,
        request_id: str,
        expected_revision: int,
    ) -> KernelMutationResult:
        response = await _request(
            "kernel",
            self._client,
            "POST",
            f"{await self._base_url()}/api/kernel/v1/device-mounts/devices/"
            f"{quote(device_id, safe='')}/unmount",
            timeout=self._timeout,
            headers=self._headers(owner_id),
            json={
                "operation": "device.unmount",
                "request_id": request_id,
                "expected_revision": expected_revision,
            },
        )
        result = _parse("kernel", response, KernelMutationResult)
        if (
            result.mount.device_id != device_id
            or result.mount.owner_id != owner_id
            or result.mount.active
        ):
            raise _contract_violation(
                "kernel",
                "Kernel Unmount response did not confirm the device is no longer mounted",
            )
        return result

    async def list_mounts(self, *, owner_id: str, limit: int = 100) -> KernelMountPage:
        response = await _request(
            "kernel",
            self._client,
            "GET",
            f"{await self._base_url()}/api/kernel/v1/device-mounts?active_only=false&limit={limit}",
            timeout=self._timeout,
            headers=self._headers(owner_id),
        )
        page = _parse("kernel", response, KernelMountPage)
        if any(mount.owner_id != owner_id for mount in page.mounts):
            raise _contract_violation(
                "kernel", "Kernel Mount page crossed the requested owner scope"
            )
        return page
