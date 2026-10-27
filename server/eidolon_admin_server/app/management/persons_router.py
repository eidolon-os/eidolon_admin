"""Person management through the existing Data authority and Local API credential."""

from urllib.parse import quote
from fastapi import APIRouter, Depends, Request, Query
from pydantic import RootModel
from eidolon_sdk.biz.person import (
    PersonCreate,
    PersonUpdate,
    PersonView,
    PersonCollection,
    PersonDeletion,
)
from eidolon_admin_server.app.service_auth import require_local_api_credential

router = APIRouter(
    prefix="/internal/v1/management/persons",
    dependencies=[Depends(require_local_api_credential)],
)


@router.get("", response_model=PersonCollection)
async def list_people(request: Request, owner_id: str):
    result = await request.app.state.control_plane.data.person_call(
        "GET", owner_id, "", RootModel[list[PersonView]]
    )
    return PersonCollection(persons=result.root)


@router.post("", response_model=PersonView)
async def create(request: Request, owner_id: str, payload: PersonCreate):
    return await request.app.state.control_plane.data.person_call(
        "POST", owner_id, "", PersonView, payload.model_dump(mode="json")
    )


@router.get("/{person_id}", response_model=PersonView)
async def get(request: Request, owner_id: str, person_id: str):
    return await request.app.state.control_plane.data.person_call(
        "GET", owner_id, "/" + quote(person_id, safe=""), PersonView
    )


@router.put("/{person_id}", response_model=PersonView)
async def update(
    request: Request, owner_id: str, person_id: str, payload: PersonUpdate
):
    return await request.app.state.control_plane.data.person_call(
        "PUT",
        owner_id,
        "/" + quote(person_id, safe=""),
        PersonView,
        payload.model_dump(mode="json"),
    )


@router.delete("/{person_id}", response_model=PersonDeletion)
async def delete(
    request: Request,
    owner_id: str,
    person_id: str,
    expected_revision: int = Query(ge=1),
):
    await request.app.state.control_plane.data.person_call(
        "DELETE",
        owner_id,
        "/" + quote(person_id, safe=""),
        None,
        params={"expected_revision": str(expected_revision)},
    )
    return PersonDeletion()
