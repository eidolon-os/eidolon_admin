from eidolon_sdk.biz.person_face import PersonFaceState, PersonFaceUpdate, PersonFaceImage
"""Person management through the existing Data authority and Local API credential."""

from urllib.parse import quote
from typing import Literal
from eidolon_sdk.biz.system_data.models import PersonalMemoryRealmSnapshot, PersonalMemoryState
from fastapi import APIRouter, Depends, Request, Query, Response
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


@router.get("/{person_id}/memory-state", response_model=PersonalMemoryState)
async def memory_state(request: Request, owner_id: str, person_id: str):
    return await request.app.state.control_plane.data.person_call(
        "GET", owner_id, "/" + quote(person_id, safe="") + "/memory-state", PersonalMemoryState)


@router.put("/{person_id}/memory-realm", response_model=PersonalMemoryRealmSnapshot)
async def enable_memory(request: Request, owner_id: str, person_id: str):
    return await request.app.state.control_plane.data.person_call(
        "PUT", owner_id, "/" + quote(person_id, safe="") + "/memory-realm", PersonalMemoryRealmSnapshot)


@router.post("/{person_id}/memory-realm/{action}")
async def change_memory(request: Request, owner_id: str, person_id: str,
                        action: Literal["suspend", "restore"], expected_revision: int = Query(ge=1)):
    model = PersonalMemoryState if action == "suspend" else PersonalMemoryRealmSnapshot
    return await request.app.state.control_plane.data.person_call(
        "POST", owner_id, "/" + quote(person_id, safe="") + "/memory-realm/" + action,
        model, params={"expected_revision": str(expected_revision)})

@router.get("/{person_id}/face-profile", response_model=PersonFaceState)
async def face_state(request: Request, owner_id: str, person_id: str, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return await request.app.state.control_plane.data.person_call(
        "GET", owner_id, "/" + quote(person_id, safe="") + "/face-profile", PersonFaceState)

@router.get("/{person_id}/face-profile/image", response_model=PersonFaceImage)
async def face_image(request: Request, owner_id: str, person_id: str, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return await request.app.state.control_plane.data.person_call(
        "GET", owner_id, "/" + quote(person_id, safe="") + "/face-profile/image", PersonFaceImage)

@router.put("/{person_id}/face-profile", response_model=PersonFaceState)
async def face_replace(request: Request, owner_id: str, person_id: str, payload: PersonFaceUpdate):
    return await request.app.state.control_plane.data.person_call(
        "PUT", owner_id, "/" + quote(person_id, safe="") + "/face-profile", PersonFaceState,
        payload.model_dump(mode="json"))

@router.delete("/{person_id}/face-profile", response_model=PersonFaceState)
async def face_delete(request: Request, owner_id: str, person_id: str, expected_revision: int = Query(ge=0)):
    return await request.app.state.control_plane.data.person_call(
        "DELETE", owner_id, "/" + quote(person_id, safe="") + "/face-profile", PersonFaceState,
        params={"expected_revision": str(expected_revision)})
