"""Person management through the existing Data authority and Local API credential."""

import base64
import binascii

from eidolon_sdk.biz.person_face import (
    PersonFaceFeaturesWrite,
    PersonFaceRecognitionEnable,
    PersonFaceState,
    PersonFaceUpdate,
    PersonFaceImage,
)


from urllib.parse import quote
from typing import Literal
from eidolon_sdk.biz.system_data.models import (
    PersonMemoryDefaults,
    PersonMemoryDefaultsState,
    PersonalMemoryRealmSnapshot,
    PersonalMemoryState,
    ProvisionalMemoryPolicy,
    ProvisionalMemoryPolicyState,
)
from fastapi import APIRouter, Depends, Request, Query, Response
from pydantic import RootModel
from eidolon_sdk.biz.person import (
    PersonCreate,
    PersonUpdate,
    PersonView,
    PersonCollection,
    PersonArchival,
)
from eidolon_admin_server.app.control_plane.clients import (
    FACE_RECOGNITION_UNAVAILABLE,
)
from eidolon_admin_server.app.control_plane.errors import AuthorityFailure
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


@router.get("/memory-policy", response_model=PersonMemoryDefaultsState)
async def memory_defaults(request: Request, owner_id: str):
    return await request.app.state.control_plane.data.person_call(
        "GET", owner_id, "/memory-policy", PersonMemoryDefaultsState,
    )


@router.put("/memory-policy", response_model=PersonMemoryDefaultsState)
async def set_memory_defaults(request: Request, owner_id: str, payload: PersonMemoryDefaults,
                              expected_revision: int = Query(ge=1)):
    return await request.app.state.control_plane.data.person_call(
        "PUT", owner_id, "/memory-policy", PersonMemoryDefaultsState,
        payload.model_dump(mode="json"), params={"expected_revision": str(expected_revision)},
    )


@router.get("/archived", response_model=PersonCollection)
async def archived(request: Request, owner_id: str):
    result = await request.app.state.control_plane.data.person_call(
        "GET", owner_id, "/archived", RootModel[list[PersonView]]
    )
    return PersonCollection(persons=result.root)


@router.post("/{person_id}/restore", response_model=PersonView)
async def restore(
    request: Request,
    owner_id: str,
    person_id: str,
    expected_revision: int = Query(ge=1),
):
    return await request.app.state.control_plane.data.person_call(
        "POST",
        owner_id,
        "/" + quote(person_id, safe="") + "/restore",
        PersonView,
        params={"expected_revision": str(expected_revision)},
    )


@router.post("/{person_id}/recognition/retire", response_model=PersonView)
async def retire_candidate(request: Request, owner_id: str, person_id: str,
                           expected_revision: int = Query(ge=1)):
    return await request.app.state.control_plane.data.person_call(
        "POST", owner_id, "/" + quote(person_id, safe="") + "/recognition/retire",
        PersonView, params={"expected_revision": str(expected_revision)},
    )


@router.post("/{person_id}/permanent-delete")
async def permanent_delete(
    request: Request,
    owner_id: str,
    person_id: str,
    expected_revision: int = Query(ge=1),
):
    cp = request.app.state.control_plane
    suffix = "/" + quote(person_id, safe="") + "/permanent-delete/"
    result = await cp.data.person_call(
        "POST",
        owner_id,
        suffix + "begin",
        RootModel[dict],
        params={"expected_revision": str(expected_revision)},
    )
    intent = result.root
    if intent["state"] == "completed":
        return intent
    await cp.memory_supervisor.erase_person(
        realm_id=intent["realm_id"], owner_id=owner_id, person_id=person_id
    )
    completed = await cp.data.person_call(
        "POST",
        owner_id,
        suffix + "complete",
        RootModel[dict],
        params={"expected_revision": str(intent["revision"])},
    )
    return completed.root


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


@router.post("/{person_id}/archive", response_model=PersonArchival)
async def delete(
    request: Request,
    owner_id: str,
    person_id: str,
    expected_revision: int = Query(ge=1),
):
    await request.app.state.control_plane.data.person_call(
        "POST",
        owner_id,
        "/" + quote(person_id, safe="") + "/archive",
        None,
        params={"expected_revision": str(expected_revision)},
    )
    return PersonArchival()


@router.get("/{person_id}/provisional-memory/policy", response_model=ProvisionalMemoryPolicyState)
async def provisional_policy(request: Request, owner_id: str, person_id: str):
    return await request.app.state.control_plane.data.person_call(
        "GET", owner_id, "/" + quote(person_id, safe="") + "/provisional-memory/policy",
        ProvisionalMemoryPolicyState,
    )


@router.put("/{person_id}/provisional-memory/policy", response_model=ProvisionalMemoryPolicyState)
async def set_provisional_policy(request: Request, owner_id: str, person_id: str,
                                 payload: ProvisionalMemoryPolicy,
                                 expected_revision: int = Query(ge=1)):
    return await request.app.state.control_plane.data.person_call(
        "PUT", owner_id, "/" + quote(person_id, safe="") + "/provisional-memory/policy",
        ProvisionalMemoryPolicyState, payload.model_dump(mode="json"),
        params={"expected_revision": str(expected_revision)},
    )


@router.get("/{person_id}/memory-state", response_model=PersonalMemoryState)
async def memory_state(request: Request, owner_id: str, person_id: str):
    return await request.app.state.control_plane.data.person_call(
        "GET",
        owner_id,
        "/" + quote(person_id, safe="") + "/memory-state",
        PersonalMemoryState,
    )


@router.put("/{person_id}/memory-realm", response_model=PersonalMemoryRealmSnapshot)
async def enable_memory(request: Request, owner_id: str, person_id: str):
    return await request.app.state.control_plane.data.person_call(
        "PUT",
        owner_id,
        "/" + quote(person_id, safe="") + "/memory-realm",
        PersonalMemoryRealmSnapshot,
    )


@router.post("/{person_id}/memory-realm/{action}")
async def change_memory(
    request: Request,
    owner_id: str,
    person_id: str,
    action: Literal["suspend", "restore"],
    expected_revision: int = Query(ge=1),
):
    model = PersonalMemoryState if action == "suspend" else PersonalMemoryRealmSnapshot
    return await request.app.state.control_plane.data.person_call(
        "POST",
        owner_id,
        "/" + quote(person_id, safe="") + "/memory-realm/" + action,
        model,
        params={"expected_revision": str(expected_revision)},
    )


@router.get("/{person_id}/face-profile", response_model=PersonFaceState)
async def face_state(
    request: Request, owner_id: str, person_id: str, response: Response
):
    response.headers["Cache-Control"] = "no-store"
    return await request.app.state.control_plane.data.person_call(
        "GET",
        owner_id,
        "/" + quote(person_id, safe="") + "/face-profile",
        PersonFaceState,
    )


@router.get("/{person_id}/face-profile/image", response_model=PersonFaceImage)
async def face_image(
    request: Request, owner_id: str, person_id: str, response: Response
):
    response.headers["Cache-Control"] = "no-store"
    return await request.app.state.control_plane.data.person_call(
        "GET",
        owner_id,
        "/" + quote(person_id, safe="") + "/face-profile/image",
        PersonFaceImage,
    )


@router.put("/{person_id}/face-profile", response_model=PersonFaceState)
async def face_replace(
    request: Request, owner_id: str, person_id: str, payload: PersonFaceUpdate
):
    return await request.app.state.control_plane.data.person_call(
        "PUT",
        owner_id,
        "/" + quote(person_id, safe="") + "/face-profile",
        PersonFaceState,
        payload.model_dump(mode="json"),
    )


@router.delete("/{person_id}/face-profile", response_model=PersonFaceState)
async def face_delete(
    request: Request,
    owner_id: str,
    person_id: str,
    expected_revision: int = Query(ge=0),
):
    return await request.app.state.control_plane.data.person_call(
        "DELETE",
        owner_id,
        "/" + quote(person_id, safe="") + "/face-profile",
        PersonFaceState,
        params={"expected_revision": str(expected_revision)},
    )


@router.put("/{person_id}/face-profile/recognition", response_model=PersonFaceState)
async def face_recognition_enable(
    request: Request,
    owner_id: str,
    person_id: str,
    payload: PersonFaceRecognitionEnable,
    response: Response,
):
    """Derive a feature from the stored photo and record it with the consent.

    Admin orchestrates and keeps nothing: the photo comes from Data, Vision
    turns it into a feature, and Data stores that next to the consent. The
    revision is checked here so a photo replaced since the Owner agreed is
    never sent to Vision, and again by Data so a replacement during the call
    cannot be enabled under the old consent.
    """

    response.headers["Cache-Control"] = "no-store"
    plane = request.app.state.control_plane
    base = "/" + quote(person_id, safe="") + "/face-profile"
    image = await plane.data.person_call("GET", owner_id, base + "/image", PersonFaceImage)
    if not image.image_base64 or image.revision != payload.expected_revision:
        raise AuthorityFailure(
            "data", "conflict", "face profile changed; refresh before retrying", 409
        )
    vision = getattr(plane, "vision", None)
    if vision is None:
        raise AuthorityFailure(
            "vision",
            "configuration",
            "face recognition is not configured on this Host",
            503,
            code=FACE_RECOGNITION_UNAVAILABLE,
        )
    try:
        photo = base64.b64decode(image.image_base64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise AuthorityFailure(
            "data", "contract_violation", "data returned an unreadable photo", 502
        ) from exc
    features = await vision.embed(owner_id=owner_id, image=photo)
    write = PersonFaceFeaturesWrite(
        expected_revision=payload.expected_revision,
        # Data stamps when the consent was recorded; a client's time is dropped.
        consent=payload.consent.model_copy(update={"accepted_at": None}),
        features=features,
    )
    return await plane.data.person_call(
        "PUT",
        owner_id,
        base + "/recognition",
        PersonFaceState,
        write.model_dump(mode="json"),
    )


@router.delete("/{person_id}/face-profile/recognition", response_model=PersonFaceState)
async def face_recognition_disable(
    request: Request, owner_id: str, person_id: str, response: Response
):
    response.headers["Cache-Control"] = "no-store"
    return await request.app.state.control_plane.data.person_call(
        "DELETE",
        owner_id,
        "/" + quote(person_id, safe="") + "/face-profile/recognition",
        PersonFaceState,
    )
