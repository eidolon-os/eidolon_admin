"""Transport adapters for Data, Hub and Kernel public application contracts."""

from __future__ import annotations

from collections import deque
from typing import Any, Literal, TypeVar
from urllib.parse import quote

import httpx
from eidolon_sdk.biz.system_data import CompanionRuntimeSnapshot
from pydantic import BaseModel, ValidationError

from .contracts import (
    MemoryBrowse,
    PersonaChapter,
    PersonaTimeline,
    CompanionIdentity,
    CompanionProvision,
    CompanionRosterPage,
    HubDevice,
    DeviceRef,
    HubClaimRevocationResult,
    HubDeviceControlOperationStatus,
    HubDeviceEvent,
    HubDeviceEventPage,
    HubDevicePage,
    HubLifecycleStatus,
    KernelMountPage,
    CompanionFace,
    KernelMutationResult,
    OwnerIdentity,
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
    content: bytes | None = None,
    params: dict[str, str] | None = None,
) -> httpx.Response:
    try:
        return await client.request(
            method,
            url,
            timeout=timeout,
            headers=headers,
            json=json,
            content=content,
            params=params,
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

    async def list_owner_companions(
        self,
        owner_id: str,
        *,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> CompanionRosterPage:
        """One page of this Owner's roster, from the authority that owns it.

        ``owner_id`` is in the path and the authority filters by it, so this is
        not a client-side scope check that could be forgotten: a Companion of
        another Owner is not something this call can return.
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
        # Forwarded as received. Admin neither decodes the cursor nor decides
        # the page size default — the authority owns both, and a second opinion
        # here would be a second page boundary.
        params: dict[str, str] = {}
        if cursor is not None:
            params["cursor"] = cursor
        if limit is not None:
            params["limit"] = str(limit)
        response = await _request(
            "data",
            self._client,
            "GET",
            f"{endpoint.address.rstrip('/')}/api/companion-authority/v1/owners/"
            f"{quote(owner_id, safe='')}/companions",
            timeout=self._timeout,
            headers={"Authorization": f"Bearer {self._token}"},
            params=params or None,
        )
        page = _parse("data", response, CompanionRosterPage)
        if page.owner_id != owner_id:
            raise _contract_violation(
                "data", "Data returned a roster for a different Owner"
            )
        return page

    async def get_owner_companion(
        self,
        owner_id: str,
        companion_id: str,
    ) -> CompanionIdentity:
        """One Companion, with ownership proved by the authority.

        Not the same as ``get_companion`` plus a comparison here. That route
        exists for Kernel, which asks "may this be assigned" and compares
        Owners itself; a product surface must not be trusted to do that
        comparison, so the Owner is in the path and the authority checks it.
        A Companion of another Owner comes back as absent rather than as
        forbidden, so an id cannot be probed for existence.
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
            "GET",
            f"{endpoint.address.rstrip('/')}/api/companion-authority/v1/owners/"
            f"{quote(owner_id, safe='')}/companions/{quote(companion_id, safe='')}",
            timeout=self._timeout,
            headers={"Authorization": f"Bearer {self._token}"},
        )
        identity = _parse("data", response, CompanionIdentity)
        if identity.companion_id != companion_id or identity.owner_id != owner_id:
            raise _contract_violation(
                "data", "Data returned a different Companion than was asked for"
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

    async def get_companion_face_state(self, companion_id: str) -> CompanionFace:
        return await self._companion_call(
            "GET",
            f"{companion_id}/face-state",
            companion_id,
            CompanionFace,
        )

    async def get_companion_face(self, companion_id: str) -> bytes | None:
        """The face itself, or None when this Companion has none.

        Bytes are returned as bytes. Every layer between Data and the phone
        would otherwise have to encode and decode a photograph to say the same
        thing, and none of them has any use for what is inside it.
        """

        response = await self._face_request("GET", companion_id)
        if response.status_code == 204:
            return None
        if response.status_code == 404:
            raise AuthorityFailure(
                "data", "not_found", "companion not found", 404, retryable=False
            )
        if response.status_code != 200:
            raise _contract_violation("data", "Data did not serve the Companion face")
        return response.content

    async def set_companion_face(self, companion_id: str, face: bytes) -> CompanionFace:
        response = await self._face_request(
            "PUT",
            companion_id,
            content=face,
            headers={"Content-Type": "image/jpeg"},
        )
        if response.status_code in (413, 415, 422):
            raise AuthorityFailure(
                "data",
                "rejected",
                "companion face was refused",
                response.status_code,
                retryable=False,
            )
        return _parse("data", response, CompanionFace)

    async def clear_companion_face(self, companion_id: str) -> CompanionFace:
        return _parse("data", await self._face_request("DELETE", companion_id), CompanionFace)

    async def _face_request(
        self,
        method: str,
        companion_id: str,
        *,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
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
        return await _request(
            "data",
            self._client,
            method,
            f"{endpoint.address.rstrip('/')}/api/companion-authority/v1/companions/"
            f"{quote(companion_id, safe='')}/face",
            timeout=self._timeout,
            headers={"Authorization": f"Bearer {self._token}", **(headers or {})},
            content=content,
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

    async def get_owner_default_runtime(
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
            f"{quote(owner_id, safe='')}/default-runtime-snapshot",
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

    async def get_owner(self, owner_id: str) -> OwnerIdentity:
        return await self._owner_call("GET", owner_id)

    async def rename_owner(self, owner_id: str, display_name: str) -> OwnerIdentity:
        """Set what this Owner is called.

        As with a Companion, whether the caller may rename *this* Owner is not
        decided here: this client speaks to Data on Admin's behalf, and whose
        Owner it is belongs at the Local API boundary.
        """

        return await self._owner_call(
            "PATCH", owner_id, json={"display_name": display_name}
        )

    async def provision_companion(
        self,
        owner_id: str,
        *,
        operation_id: str,
        companion_display_name: str,
        kind: str,
    ) -> CompanionProvision:
        """Add a Companion to this Owner, exactly once per operation id.

        The operation id is the caller's, not ours: it is what makes a retry
        idempotent, and generating one here would make every retry a new
        operation. The authority derives every identifier from it.
        """

        response = await _request(
            "data",
            self._client,
            "PUT",
            f"{await self._base_url()}/api/workspace-authority/v1/owners/"
            f"{quote(owner_id, safe='')}/companion-provisions/"
            f"{quote(operation_id, safe='')}",
            timeout=self._timeout,
            headers=self._headers,
            json={
                "companion_display_name": companion_display_name,
                "kind": kind,
            },
        )
        result = _parse("data", response, CompanionProvision)
        if result.operation_id != operation_id:
            raise _contract_violation(
                "data", "Data returned a different provision operation"
            )
        return result

    async def set_default_companion(
        self,
        owner_id: str,
        *,
        companion_id: str,
        expected_revision: int,
    ) -> OwnerIdentity:
        """Point this Owner's unaddressed work at one of their Companions.

        The whole write is one field on the Owner, and the authority decides
        everything about whether it may happen: that the Companion is this
        Owner's, that it is not a guard, and that the revision the caller read
        is still current. None of those are re-checked here — a second opinion
        would be a second answer.
        """

        response = await _request(
            "data",
            self._client,
            "PUT",
            f"{await self._base_url()}/api/workspace-authority/v1/owners/"
            f"{quote(owner_id, safe='')}/default-companion",
            timeout=self._timeout,
            headers=self._headers,
            json={
                "companion_id": companion_id,
                "expected_revision": expected_revision,
            },
        )
        identity = _parse("data", response, OwnerIdentity)
        if identity.owner_id != owner_id:
            raise _contract_violation("data", "Data returned a different Owner identity")
        if identity.default_companion_id != companion_id:
            # The authority answered 200 for a state that is not the one asked
            # for. Better to refuse than to relay it: a client would show the
            # change as done.
            raise _contract_violation(
                "data", "Data accepted the default change without applying it"
            )
        return identity

    async def _owner_call(
        self,
        method: str,
        owner_id: str,
        **kwargs: Any,
    ) -> OwnerIdentity:
        response = await _request(
            "data",
            self._client,
            method,
            f"{await self._base_url()}/api/workspace-authority/v1/owners/"
            f"{quote(owner_id, safe='')}",
            timeout=self._timeout,
            headers=self._headers,
            **kwargs,
        )
        identity = _parse("data", response, OwnerIdentity)
        if identity.owner_id != owner_id:
            raise _contract_violation("data", "Data returned a different Owner identity")
        return identity

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


def _realm_path(published_url: str, leaf: str) -> str:
    """A sibling route on the space's own port.

    Discovery publishes one URL per space — historically the recollections one —
    and the rest of ``/api/memory/v1/*`` sits beside it on that port. Composing
    from the prefix rather than substituting into the URL keeps this from
    rewriting part of a host or a query, and keeps Admin from guessing at the
    realm's route layout.
    """

    prefix, separator, _ = published_url.rpartition("/api/memory/v1/")
    if not separator:
        raise _contract_violation(
            "memory", "memory discovery published a read surface outside the contract"
        )
    return f"{prefix}{separator}{leaf}"


def _realm_name(realm: dict) -> str:
    """How a Realm is named in a failure: by its id, never by its address."""
    for key in ("memory_realm_id", "memory_space_id"):
        value = realm.get(key)
        if isinstance(value, str) and value:
            return value
    return "unidentified"


class MemorySupervisorClient:
    """Asks the memory supervisor to bring the roster's Realms up.

    This is an **accelerator, not the guarantee**. The supervisor re-reads the
    authority roster on its own schedule, so a Realm catalogued while it was
    running converges without anyone telling it (that control loop is what
    `eidolon_memory@c47f8de` fixed). Asking just removes the wait.

    Which is why every failure here is reported and swallowed by the caller
    rather than failing the create: a Companion that exists in the authority
    with its Realm not yet running is a correct intermediate state, and undoing
    the create because a notification did not land would be worse than being
    slow.
    """

    def __init__(
        self,
        *,
        base_url: str,
        client: httpx.AsyncClient,
        timeout_seconds: float,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client
        self._timeout = timeout_seconds

    async def request_reconcile(self) -> bool:
        """True when the supervisor accepted; False when it could not be told.

        A bool rather than an exception: the caller has nothing to decide, only
        something to report, and an exception here would invite someone to make
        it fatal.
        """

        try:
            response = await self._client.post(
                f"{self._base_url}/api/admin/reconcile", timeout=self._timeout
            )
        except (
            httpx.TimeoutException,
            httpx.NetworkError,
            httpx.RemoteProtocolError,
        ):
            return False
        return response.status_code == 200


class MemoryRecollectionsClient:
    """What an Owner's Eidolon remembers, read through the memory service.

    Two hops rather than one, because memory is addressed by space rather than
    by service: discovery says which space belongs to this Owner and where it
    answers, and the space itself answers the question. The System Directory is
    not asked, because it maps services to endpoints and there is one endpoint
    per space here, not per service.
    """

    def __init__(
        self,
        *,
        discovery_url: str,
        client: httpx.AsyncClient,
        timeout_seconds: float,
        service_token: str = "",
    ) -> None:
        self._discovery_url = discovery_url.rstrip("/")
        self._client = client
        self._timeout = timeout_seconds
        #: The realm surface's own credential. Discovery stays unauthenticated —
        #: it publishes where a space lives, which is not the space's contents —
        #: and only the read of what a person remembers presents this.
        self._service_token = service_token.strip()

    async def recollections(
        self,
        *,
        owner_id: str,
        query: str,
        limit: int,
        companion_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """What this Owner's memory holds about a query.

        ``companion_id`` selects an audience, not a scope: the space is the
        Owner's either way, and naming a Companion adds that Companion's own
        statements to the Owner-layer ones. Omitting it answers with the Owner
        layer, which is what an Owner-level question wants.
        """
        if not self._service_token:
            raise AuthorityFailure(
                "memory",
                "configuration",
                "Admin memory service credential is not configured",
                503,
                retryable=False,
            )
        space = await self._space_for(owner_id)
        params = {"q": query, "limit": str(limit)}
        if companion_id:
            params["companion_id"] = companion_id
        response = await _request(
            "memory",
            self._client,
            "GET",
            _realm_path(space, "recollections"),
            headers={"Authorization": f"Bearer {self._service_token}"},
            timeout=self._timeout,
            params=params,
        )
        if response.status_code != 200:
            raise AuthorityFailure(
                "memory",
                "unavailable",
                "memory did not answer",
                503,
                upstream_status=response.status_code,
                retryable=True,
            )
        try:
            payload = response.json()
            recollections = payload["recollections"]
        except (ValueError, KeyError, TypeError) as exc:
            raise _contract_violation(
                "memory", "memory answered outside its contract"
            ) from exc
        if not isinstance(recollections, list):
            raise _contract_violation("memory", "memory answered outside its contract")
        return recollections

    async def browse(
        self,
        *,
        owner_id: str,
        companion_id: str | None = None,
    ) -> MemoryBrowse:
        """What this Owner's memory holds, as the realm reports it.

        The realm applies the same visibility policy recall does, so this client
        does no filtering of its own — a second filter here would be a second
        answer to "what may this person see", and the two would drift.
        """

        if not self._service_token:
            raise AuthorityFailure(
                "memory",
                "configuration",
                "Admin memory service credential is not configured",
                503,
                retryable=False,
            )
        space = await self._space_for(owner_id)
        browse_url = space.replace("/recollections", "/browse")
        if browse_url == space:
            raise _contract_violation(
                "memory", "memory discovery published no browsable read surface"
            )
        params = {"companion_id": companion_id} if companion_id else None
        response = await _request(
            "memory",
            self._client,
            "GET",
            _realm_path(space, "browse"),
            timeout=self._timeout,
            headers={"Authorization": f"Bearer {self._service_token}"},
            params=params,
        )
        return _parse("memory", response, MemoryBrowse)

    async def _space_for(self, owner_id: str) -> str:
        """The one memory space this Owner has, or a failure naming why not.

        Memory is Owner-level: one Owner, one Realm, read by every Companion
        that Owner has (docs/跨系统/多Companion记忆隔离机制裁决.md). So this is a
        uniqueness check, not a search. Two matches mean the Owner-level
        migration is incomplete or the data is wrong, and either answer would
        be a coin flip between two Companions' memories — so it refuses rather
        than taking whichever sorted first.
        """
        response = await _request(
            "memory",
            self._client,
            "GET",
            f"{self._discovery_url}/api/discovery/agent-routing",
            timeout=self._timeout,
        )
        if response.status_code != 200:
            raise AuthorityFailure(
                "memory",
                "unavailable",
                "memory discovery is unavailable",
                503,
                retryable=True,
            )
        try:
            realms = response.json()["memory_realms"]
        except (ValueError, KeyError, TypeError) as exc:
            raise _contract_violation(
                "memory", "memory discovery answered outside its contract"
            ) from exc
        if not isinstance(realms, list):
            raise _contract_violation(
                "memory", "memory discovery answered outside its contract"
            )
        owned = [
            realm
            for realm in realms
            if isinstance(realm, dict) and realm.get("owner_id") == owner_id
        ]
        if not owned:
            raise AuthorityFailure(
                "memory",
                "not_found",
                "this Owner has no memory space",
                404,
                retryable=False,
            )
        if len(owned) > 1:
            named = ", ".join(sorted(_realm_name(realm) for realm in owned))
            raise AuthorityFailure(
                "memory",
                "conflict",
                (
                    "this Owner has more than one memory space "
                    f"({named}); one Owner is one Realm, so which of these "
                    "answers cannot be decided here"
                ),
                409,
                retryable=False,
            )
        realm = owned[0]
        url = realm.get("recollections_url")
        if not isinstance(url, str) or not url:
            # A space that does not publish a read surface is one this Admin is
            # too new or too old to read; saying so beats answering as though
            # the Owner remembered nothing.
            raise _contract_violation(
                "memory", "this memory space serves no read surface"
            )
        # Discovery derives this URL from the port and reports liveness
        # separately, so a published URL says nothing about whether anything
        # is listening. Dialling it anyway turns "this Realm is not running"
        # into "memory is unreachable" — the wrong problem, and it sent people
        # to look at the wrong service.
        if not realm.get("enabled", True) or realm.get("agent_reachable") is False:
            raise AuthorityFailure(
                "memory",
                "runtime_missing",
                f"this Owner's memory space ({_realm_name(realm)}) is not running",
                503,
                retryable=True,
            )
        return url


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
        device_ref: DeviceRef,
        reason: str,
        command_id: str,
        correlation_id: str,
        authorization: str,
    ) -> HubClaimRevocationResult:
        """Submit one generation-bound Claim command."""

        base_url = await self._base_url()
        device_id = device_ref.device_instance_id
        response = await _request(
            "hub",
            self._client,
            "POST",
            f"{base_url}/api/device-management/v1/devices/{quote(device_id, safe='')}/revocation",
            timeout=self._timeout,
            headers=self._headers(authorization),
            json={
                "operation": "device.claim-revocation",
                "command_id": command_id,
                "correlation_id": correlation_id,
                "device_ref": device_ref.model_dump(mode="json"),
                "reason": reason,
            },
        )
        result = _parse("hub", response, HubClaimRevocationResult)
        if (
            result.command_id != command_id
            or result.device_ref != device_ref
            or result.lifecycle_state != "revoked"
        ):
            raise _contract_violation(
                "hub",
                "Hub revocation response did not confirm the requested Claim",
            )
        return result

    async def get_device(
        self,
        *,
        owner_id: str,
        device_id: str,
        authorization: str,
    ) -> HubDevice:
        base_url = await self._base_url()
        response = await _request(
            "hub",
            self._client,
            "GET",
            f"{base_url}/api/device-management/v1/owners/{quote(owner_id, safe='')}"
            f"/devices/{quote(device_id, safe='')}",
            timeout=self._timeout,
            headers=self._headers(authorization),
            json=None,
        )
        result = _parse("hub", response, HubDevice)
        if (
            result.device_id != device_id
            or result.owner_scope != owner_id
            or result.device_ref is None
            or result.device_ref.device_instance_id != device_id
            or result.device_ref.owner_domain_id != owner_id
        ):
            raise _contract_violation(
                "hub", "Hub exact query crossed its requested Claim scope"
            )
        return result

    async def get_device_control_operation(
        self,
        *,
        device_ref: DeviceRef,
        event_id: str,
        authorization: str,
    ) -> HubDeviceControlOperationStatus:
        base_url = await self._base_url()
        response = await _request(
            "hub",
            self._client,
            "GET",
            f"{base_url}/api/device-management/v1/owners/"
            f"{quote(device_ref.owner_domain_id, safe='')}/devices/"
            f"{quote(device_ref.device_instance_id, safe='')}/control-operations/"
            f"{quote(event_id, safe='')}",
            timeout=self._timeout,
            headers=self._headers(authorization),
        )
        result = _parse("hub", response, HubDeviceControlOperationStatus)
        if result.event_id != event_id or result.device_ref != device_ref:
            raise _contract_violation(
                "hub", "Device Control status crossed its requested event/Claim scope"
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
        lifecycle_state: Literal["pending-approval", "approved", "revoked"]
        | None = None,
    ) -> HubDevicePage:
        base_url = await self._base_url()
        response = await _request(
            "hub",
            self._client,
            "GET",
            f"{base_url}/api/device-management/v1/owners/{quote(owner_id, safe='')}"
            "/devices",
            timeout=self._timeout,
            headers=self._headers(authorization),
            json=None,
            params={
                "limit": str(limit),
                **(
                    {"lifecycle_state": lifecycle_state}
                    if lifecycle_state is not None
                    else {}
                ),
            },
        )
        page = _parse("hub", response, HubDevicePage)
        if any(device.owner_scope != owner_id for device in page.devices):
            raise _contract_violation(
                "hub", "Hub directory page crossed the requested owner scope"
            )
        return page

    async def list_events(
        self,
        *,
        owner_id: str,
        authorization: str,
        after_stream_position: int = 0,
        limit: int = 500,
    ) -> HubDeviceEventPage:
        """One page of what the Hub recorded happening in this scope."""

        base_url = await self._base_url()
        response = await _request(
            "hub",
            self._client,
            "GET",
            f"{base_url}/api/device-management/v1/owners/{quote(owner_id, safe='')}"
            f"/events",
            timeout=self._timeout,
            headers=self._headers(authorization),
            params={
                "after_stream_position": str(after_stream_position),
                "limit": str(limit),
            },
        )
        return _parse("hub", response, HubDeviceEventPage)

    async def latest_events(
        self,
        *,
        owner_id: str,
        authorization: str,
        keep: int,
        page_size: int = 500,
        max_pages: int = 100,
    ) -> tuple[HubDeviceEvent, ...]:
        """The newest `keep` things recorded in this scope.

        The Hub's ledger reads forward from a position and has no "latest"
        query, so the newest is found by walking to the end. Only a trailing
        window is held.

        Walking has a bound, and reaching it is an error rather than an
        answer. Past that many events the window would hold the oldest of what
        was read instead of the newest of what exists, and a history quietly
        showing the wrong end of itself is worse than one that says it could
        not be read.
        """

        window: deque[HubDeviceEvent] = deque(maxlen=keep)
        position = 0
        for _ in range(max_pages):
            page = await self.list_events(
                owner_id=owner_id,
                authorization=authorization,
                after_stream_position=position,
                limit=page_size,
            )
            window.extend(page.events)
            if len(page.events) < page_size:
                return tuple(window)
            position = page.next_stream_position
        raise AuthorityFailure(
            "hub",
            "upstream_failure",
            "Hub device history is longer than this Host can project",
            502,
        )


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
