"""Controller-authenticated Person management, scoped to the claimed Owner."""

from eidolon_sdk.biz.person_face import (
    PersonFaceState,
    PersonFaceUpdate,
    PersonFaceImage,
)


from urllib.parse import quote
from typing import Literal
from eidolon_sdk.biz.system_data.models import (
    PersonalMemoryRealmSnapshot,
    PersonalMemoryState,
)
from fastapi import Header, Query, Response
from eidolon_sdk.biz.person import (
    PersonCreate,
    PersonUpdate,
    PersonView,
    PersonCollection,
    PersonDeletion,
)
from .backend import ManagementBackendError


from pydantic import BaseModel


class PersonMemoryPurgeView(BaseModel):
    realm_id: str
    person_id: str
    revision: int
    state: Literal["pending", "completed"]


def register_person_routes(router, backend, authenticated_owner, refused):
    async def invoke(
        authorization, suffix, method="GET", payload=None, expected_revision=None
    ):
        owner = await authenticated_owner(authorization)
        try:
            return await backend.person_call(
                owner_id=owner,
                suffix=suffix,
                method=method,
                payload=payload,
                expected_revision=expected_revision,
            )
        except ManagementBackendError as exc:
            raise refused(exc) from exc

    @router.get("/persons", response_model=PersonCollection)
    async def list_people(authorization: str | None = Header(default=None)):
        return await invoke(authorization, "")

    @router.post("/persons", response_model=PersonView)
    async def create(
        payload: PersonCreate, authorization: str | None = Header(default=None)
    ):
        return await invoke(authorization, "", "POST", payload.model_dump(mode="json"))

    @router.get("/persons/archived", response_model=PersonCollection)
    async def archived(authorization: str | None = Header(default=None)):
        return await invoke(authorization, "/archived")

    @router.post("/persons/{person_id}/restore", response_model=PersonView)
    async def restore(
        person_id: str,
        expected_revision: int = Query(ge=1),
        authorization: str | None = Header(default=None),
    ):
        return await invoke(
            authorization,
            "/" + quote(person_id, safe="") + "/restore",
            "POST",
            expected_revision=expected_revision,
        )

    @router.post(
        "/persons/{person_id}/memory-purge", response_model=PersonMemoryPurgeView
    )
    async def purge_memory(
        person_id: str,
        expected_revision: int = Query(ge=1),
        authorization: str | None = Header(default=None),
    ):
        return await invoke(
            authorization,
            "/" + quote(person_id, safe="") + "/memory-purge",
            "POST",
            expected_revision=expected_revision,
        )

    @router.get("/persons/{person_id}", response_model=PersonView)
    async def get(person_id: str, authorization: str | None = Header(default=None)):
        return await invoke(authorization, "/" + quote(person_id, safe=""))

    @router.put("/persons/{person_id}", response_model=PersonView)
    async def update(
        person_id: str,
        payload: PersonUpdate,
        authorization: str | None = Header(default=None),
    ):
        return await invoke(
            authorization,
            "/" + quote(person_id, safe=""),
            "PUT",
            payload.model_dump(mode="json"),
        )

    @router.delete("/persons/{person_id}", response_model=PersonDeletion)
    async def delete(
        person_id: str,
        expected_revision: int = Query(ge=1),
        authorization: str | None = Header(default=None),
    ):
        return await invoke(
            authorization,
            "/" + quote(person_id, safe=""),
            "DELETE",
            expected_revision=expected_revision,
        )

    @router.get("/persons/{person_id}/memory-state", response_model=PersonalMemoryState)
    async def memory_state(
        person_id: str, authorization: str | None = Header(default=None)
    ):
        return await invoke(
            authorization, "/" + quote(person_id, safe="") + "/memory-state"
        )

    @router.put(
        "/persons/{person_id}/memory-realm", response_model=PersonalMemoryRealmSnapshot
    )
    async def enable_memory(
        person_id: str, authorization: str | None = Header(default=None)
    ):
        return await invoke(
            authorization, "/" + quote(person_id, safe="") + "/memory-realm", "PUT"
        )

    @router.post("/persons/{person_id}/memory-realm/{action}")
    async def change_memory(
        person_id: str,
        action: Literal["suspend", "restore"],
        expected_revision: int = Query(ge=1),
        authorization: str | None = Header(default=None),
    ):
        return await invoke(
            authorization,
            "/" + quote(person_id, safe="") + "/memory-realm/" + action,
            "POST",
            expected_revision=expected_revision,
        )

    @router.get("/persons/{person_id}/face-profile", response_model=PersonFaceState)
    async def face_state(
        person_id: str,
        response: Response,
        authorization: str | None = Header(default=None),
    ):
        response.headers["Cache-Control"] = "no-store"
        return await invoke(
            authorization, "/" + quote(person_id, safe="") + "/face-profile"
        )

    @router.get(
        "/persons/{person_id}/face-profile/image", response_model=PersonFaceImage
    )
    async def face_image(
        person_id: str,
        response: Response,
        authorization: str | None = Header(default=None),
    ):
        response.headers["Cache-Control"] = "no-store"
        return await invoke(
            authorization, "/" + quote(person_id, safe="") + "/face-profile/image"
        )

    @router.put("/persons/{person_id}/face-profile", response_model=PersonFaceState)
    async def face_replace(
        person_id: str,
        payload: PersonFaceUpdate,
        authorization: str | None = Header(default=None),
    ):
        return await invoke(
            authorization,
            "/" + quote(person_id, safe="") + "/face-profile",
            "PUT",
            payload.model_dump(mode="json"),
        )

    @router.delete("/persons/{person_id}/face-profile", response_model=PersonFaceState)
    async def face_delete(
        person_id: str,
        expected_revision: int = Query(ge=0),
        authorization: str | None = Header(default=None),
    ):
        return await invoke(
            authorization,
            "/" + quote(person_id, safe="") + "/face-profile",
            "DELETE",
            expected_revision=expected_revision,
        )
