"""Owner-scoped runtime projection for the Mobile product boundary."""

from __future__ import annotations

from typing import Literal, Protocol
from urllib.parse import quote

import httpx
from eidolon_sdk.biz.system_data import CompanionRuntimeSnapshot
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ..app.control_plane.contracts import (
    CompanionIdentity,
    PersonaChapter,
    PersonaTimeline,
    WorkspaceOperation,
    WorkspaceOwner,
)


class WorkspaceRuntimeError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 503) -> None:
        super().__init__(message)
        self.status_code = status_code


class AdminOwnerRuntimePort(Protocol):
    async def get_owner_primary_runtime(
        self,
        owner_id: str,
    ) -> CompanionRuntimeSnapshot: ...

    async def get_companion(self, companion_id: str) -> CompanionIdentity: ...

    async def rename_companion(
        self,
        companion_id: str,
        display_name: str,
    ) -> CompanionIdentity: ...

    async def get_persona_timeline(self, companion_id: str) -> PersonaTimeline: ...

    async def restore_persona(
        self,
        companion_id: str,
        genome_id: str,
        change_summary: str,
    ) -> PersonaChapter: ...

    async def close(self) -> None: ...


class PrimaryCompanionView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    companion_id: str = Field(min_length=1, max_length=64)
    #: What the Owner calls this Eidolon. Empty only on a Host whose Data
    #: predates answering with it, and an empty name is left for the client to
    #: handle rather than filled in with an identifier here.
    display_name: str = Field(default="", max_length=128)
    lifecycle_state: Literal["active"]


class PersonaChapterView(BaseModel):
    """One thing this Eidolon has been, as its Owner reads it.

    No genome hash, no schema version, no realizer. What a person wonders is
    when it changed and what changed — and, if they are about to go back,
    which one it is now.
    """

    model_config = ConfigDict(extra="forbid")

    chapter_id: str = Field(min_length=1, max_length=64)
    changed_at: str
    #: Written with the change, by whatever made it. Empty when nothing was
    #: recorded — left empty rather than filled in with a summary this layer
    #: invented, because a sentence about who your Eidolon became should not
    #: be written by a projection.
    what_changed: str = Field(default="", max_length=4096)
    restored_from: int | None = None
    is_current: bool = False


class PersonaHistoryView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Literal["local.persona-history"] = "local.persona-history"
    contract_version: Literal["1"] = "1"
    companion_id: str = Field(min_length=1, max_length=64)
    chapters: tuple[PersonaChapterView, ...] = ()


class PersonaRestoreCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"] = "1"
    chapter_id: str = Field(min_length=1, max_length=64)


def persona_history_view(timeline: PersonaTimeline) -> PersonaHistoryView:
    """Project the authority's timeline into what an Owner is shown.

    Proposals are dropped here. A Companion that is considering a change has
    not changed, and putting that in front of someone turns living with an
    Eidolon into reviewing it — which is the one thing this product decided
    not to ask of them. The authority still stores them; the agent still needs
    somewhere to stage. It is simply not a thing the person is handed.
    """

    return PersonaHistoryView(
        companion_id=timeline.companion_id,
        chapters=tuple(
            PersonaChapterView(
                chapter_id=chapter.genome_id,
                changed_at=chapter.created_at,
                what_changed=chapter.change_summary,
                restored_from=chapter.restored_from_version,
                is_current=chapter.is_current,
            )
            for chapter in timeline.chapters
            if chapter.lifecycle_state == "committed"
        ),
    )


class CompanionRenameCommand(BaseModel):
    """What to call this Companion, as the person typed it.

    A name of only spaces passes a length check and would erase the one they
    have. It is refused here rather than two services away: the answer is the
    same either way, and this is the boundary the person is actually talking
    to.
    """

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"] = "1"
    display_name: str = Field(min_length=1, max_length=128)

    @field_validator("display_name")
    @classmethod
    def _must_be_a_name(cls, value: str) -> str:
        name = value.strip()
        if not name:
            raise ValueError("display_name cannot be blank")
        return name


class CompanionNameView(BaseModel):
    """What a Companion is called, after being told what to be called."""

    model_config = ConfigDict(extra="forbid")

    operation: Literal["local.companion-name"] = "local.companion-name"
    contract_version: Literal["1"] = "1"
    companion_id: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=128)


class PersonaRuntimeView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    genome_id: str = Field(min_length=1, max_length=64)
    version: int = Field(ge=1)
    lifecycle_state: Literal["committed"]
    schema_version: str = Field(min_length=1, max_length=64)
    genome_hash: str = Field(min_length=1, max_length=80)
    realizer_version: str = Field(min_length=1, max_length=64)


class MemoryWorkspaceView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    realm_id: str = Field(min_length=1, max_length=64)
    lifecycle_state: Literal["active"]


class WorkspaceRuntimeView(BaseModel):
    """Sanitized daily-use projection; raw Persona and runtime config stay internal."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"] = "1"
    operation_id: str = Field(
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    )
    state: Literal["ready"] = "ready"
    owner: WorkspaceOwner
    primary_companion: PrimaryCompanionView
    persona: PersonaRuntimeView
    memory_workspace: MemoryWorkspaceView


class AdminOwnerRuntimeClient:
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

    async def get_owner_primary_runtime(
        self,
        owner_id: str,
    ) -> CompanionRuntimeSnapshot:
        if not self._token:
            raise WorkspaceRuntimeError(
                "Local API Admin service credential is not configured"
            )
        url = (
            f"{self._base_url}/api/control-plane/v1/owners/"
            f"{quote(owner_id, safe='')}/primary-runtime-snapshot"
        )
        try:
            response = await self._client.get(
                url,
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=self._timeout,
            )
        except (
            httpx.TimeoutException,
            httpx.NetworkError,
            httpx.RemoteProtocolError,
        ) as exc:
            raise WorkspaceRuntimeError(
                "Admin Owner runtime control plane is unavailable"
            ) from exc
        if response.status_code == 404:
            raise WorkspaceRuntimeError(
                "Owner primary Companion runtime does not exist",
                status_code=404,
            )
        if response.status_code in {409, 412}:
            raise WorkspaceRuntimeError(
                "Owner primary Companion runtime is not ready",
                status_code=409,
            )
        if response.status_code != 200:
            raise WorkspaceRuntimeError(
                "Admin Owner runtime control plane is unavailable"
            )
        try:
            runtime = CompanionRuntimeSnapshot.model_validate(response.json())
        except (ValueError, TypeError, ValidationError) as exc:
            raise WorkspaceRuntimeError(
                "Admin Owner runtime response violated its contract"
            ) from exc
        if runtime.owner_id != owner_id:
            raise WorkspaceRuntimeError(
                "Admin Owner runtime response returned another Owner"
            )
        return runtime

    async def get_companion(self, companion_id: str) -> CompanionIdentity:
        """Who this Companion is, including what its Owner calls it.

        A second call rather than a wider runtime snapshot: the snapshot is
        what a Companion needs in order to run, and a name is not that. It is
        what a person needs in order to recognise it.
        """

        if not self._token:
            raise WorkspaceRuntimeError(
                "Local API Admin service credential is not configured"
            )
        url = (
            f"{self._base_url}/api/control-plane/v1/companions/"
            f"{quote(companion_id, safe='')}"
        )
        try:
            response = await self._client.get(
                url,
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=self._timeout,
            )
        except (
            httpx.TimeoutException,
            httpx.NetworkError,
            httpx.RemoteProtocolError,
        ) as exc:
            raise WorkspaceRuntimeError(
                "Admin Companion control plane is unavailable"
            ) from exc
        if response.status_code == 404:
            raise WorkspaceRuntimeError("Companion does not exist", status_code=404)
        if response.status_code != 200:
            raise WorkspaceRuntimeError("Admin Companion control plane is unavailable")
        try:
            identity = CompanionIdentity.model_validate(response.json())
        except (ValueError, TypeError, ValidationError) as exc:
            raise WorkspaceRuntimeError(
                "Admin Companion response violated its contract"
            ) from exc
        if identity.companion_id != companion_id:
            raise WorkspaceRuntimeError(
                "Admin Companion response returned another Companion"
            )
        return identity

    async def rename_companion(
        self,
        companion_id: str,
        display_name: str,
    ) -> CompanionIdentity:
        if not self._token:
            raise WorkspaceRuntimeError(
                "Local API Admin service credential is not configured"
            )
        url = (
            f"{self._base_url}/api/control-plane/v1/companions/"
            f"{quote(companion_id, safe='')}"
        )
        try:
            response = await self._client.patch(
                url,
                headers={"Authorization": f"Bearer {self._token}"},
                json={"display_name": display_name},
                timeout=self._timeout,
            )
        except (
            httpx.TimeoutException,
            httpx.NetworkError,
            httpx.RemoteProtocolError,
        ) as exc:
            raise WorkspaceRuntimeError(
                "Admin Companion control plane is unavailable"
            ) from exc
        if response.status_code == 404:
            raise WorkspaceRuntimeError("Companion does not exist", status_code=404)
        if response.status_code == 422:
            raise WorkspaceRuntimeError(
                "Companion name was rejected", status_code=422
            )
        if response.status_code != 200:
            raise WorkspaceRuntimeError("Admin Companion control plane is unavailable")
        try:
            return CompanionIdentity.model_validate(response.json())
        except (ValueError, TypeError, ValidationError) as exc:
            raise WorkspaceRuntimeError(
                "Admin Companion response violated its contract"
            ) from exc

    async def get_persona_timeline(self, companion_id: str) -> PersonaTimeline:
        return await self._companion_request(
            "GET",
            f"{companion_id}/persona-timeline",
            PersonaTimeline,
        )

    async def restore_persona(
        self,
        companion_id: str,
        genome_id: str,
        change_summary: str,
    ) -> PersonaChapter:
        return await self._companion_request(
            "POST",
            f"{companion_id}/persona-restorations",
            PersonaChapter,
            json={"genome_id": genome_id, "change_summary": change_summary},
        )

    async def _companion_request(
        self,
        method: str,
        path: str,
        model: type,
        *,
        json: dict | None = None,
    ):
        if not self._token:
            raise WorkspaceRuntimeError(
                "Local API Admin service credential is not configured"
            )
        head, _, tail = path.partition("/")
        url = (
            f"{self._base_url}/api/control-plane/v1/companions/"
            f"{quote(head, safe='')}/{tail}"
        )
        try:
            response = await self._client.request(
                method,
                url,
                headers={"Authorization": f"Bearer {self._token}"},
                json=json,
                timeout=self._timeout,
            )
        except (
            httpx.TimeoutException,
            httpx.NetworkError,
            httpx.RemoteProtocolError,
        ) as exc:
            raise WorkspaceRuntimeError(
                "Admin Companion control plane is unavailable"
            ) from exc
        if response.status_code == 404:
            raise WorkspaceRuntimeError("Companion does not exist", status_code=404)
        if response.status_code == 409:
            raise WorkspaceRuntimeError(
                "This Eidolon is already the way it was then", status_code=409
            )
        if response.status_code != 200:
            raise WorkspaceRuntimeError("Admin Companion control plane is unavailable")
        try:
            return model.model_validate(response.json())
        except (ValueError, TypeError, ValidationError) as exc:
            raise WorkspaceRuntimeError(
                "Admin Companion response violated its contract"
            ) from exc

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def workspace_runtime_view(
    *,
    workspace: WorkspaceOperation,
    runtime: CompanionRuntimeSnapshot,
    bound_owner_id: str,
    companion_display_name: str = "",
) -> WorkspaceRuntimeView:
    if workspace.owner.owner_id != bound_owner_id or runtime.owner_id != bound_owner_id:
        raise WorkspaceRuntimeError(
            "Host Owner scope does not match its runtime authorities",
            status_code=409,
        )
    if runtime.companion_id != workspace.workspace.primary_companion_id:
        raise WorkspaceRuntimeError(
            "Workspace primary Companion does not match its runtime authority",
            status_code=409,
        )
    return WorkspaceRuntimeView(
        operation_id=workspace.operation_id,
        owner=workspace.owner,
        primary_companion=PrimaryCompanionView(
            companion_id=runtime.companion_id,
            display_name=companion_display_name,
            lifecycle_state=runtime.lifecycle_state,
        ),
        persona=PersonaRuntimeView(
            genome_id=runtime.persona_genome.genome_id,
            version=runtime.persona_genome.version,
            lifecycle_state=runtime.persona_genome.lifecycle_state,
            schema_version=runtime.persona_genome.schema_version,
            genome_hash=runtime.persona_genome.genome_hash,
            realizer_version=runtime.persona_genome.realizer_version,
        ),
        memory_workspace=MemoryWorkspaceView(
            realm_id=runtime.memory_realm.realm_id,
            lifecycle_state=runtime.memory_realm.lifecycle_state,
        ),
    )
