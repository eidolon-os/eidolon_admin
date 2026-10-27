"""Controller-authenticated Person management, scoped to the claimed Owner."""

from urllib.parse import quote
from fastapi import Header, Query
from eidolon_sdk.biz.person import (
    PersonCreate,
    PersonUpdate,
    PersonView,
    PersonCollection,
    PersonDeletion,
)
from .backend import ManagementBackendError


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
