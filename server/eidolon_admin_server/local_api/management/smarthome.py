"""Controller-authenticated mobile routes for one Owner's smart home."""

from __future__ import annotations

from urllib.parse import quote

from eidolon_sdk.biz.smarthome import Registry
from fastapi import APIRouter, Header, Query

from eidolon_admin_server.app.management.smarthome_router import (
    AreaWrite,
    DeviceWrite,
    PlacementWrite,
    RegistryWrite,
    SceneWrite,
)
from .router import ManagementBackendError, _refused


def register_smarthome_routes(app, *, backend, authenticated_owner) -> None:
    router = APIRouter(prefix="/api/management/v1/smarthome", tags=["management"])

    async def relay(authorization, *, method="GET", resource="registry", payload=None, expected_revision=None) -> Registry:
        owner_id = await authenticated_owner(authorization)
        try:
            answer = await backend.smarthome_registry(
                owner_id=owner_id,
                method=method,
                resource=resource,
                payload=payload,
                expected_revision=expected_revision,
            )
        except ManagementBackendError as exc:
            raise _refused(exc) from exc
        return Registry.model_validate(answer)

    @router.get("/registry", response_model=Registry)
    async def get_registry(authorization: str | None = Header(default=None, alias="Authorization")) -> Registry:
        return await relay(authorization)

    @router.post("/samples/apartment", response_model=Registry)
    async def load_apartment(body: RegistryWrite, authorization: str | None = Header(default=None, alias="Authorization")) -> Registry:
        return await relay(authorization, method="POST", resource="samples/apartment", payload=body.model_dump())

    @router.post("/areas", response_model=Registry)
    async def create_area(body: AreaWrite, authorization: str | None = Header(default=None, alias="Authorization")) -> Registry:
        return await relay(authorization, method="POST", resource="areas", payload=body.model_dump(mode="json"))

    @router.put("/areas/{area_id}", response_model=Registry)
    async def update_area(area_id: str, body: AreaWrite, authorization: str | None = Header(default=None, alias="Authorization")) -> Registry:
        return await relay(authorization, method="PUT", resource=f"areas/{quote(area_id, safe='')}", payload=body.model_dump(mode="json"))

    @router.delete("/areas/{area_id}", response_model=Registry)
    async def delete_area(area_id: str, expected_revision: int = Query(ge=0), authorization: str | None = Header(default=None, alias="Authorization")) -> Registry:
        return await relay(authorization, method="DELETE", resource=f"areas/{quote(area_id, safe='')}", expected_revision=expected_revision)

    @router.post("/devices", response_model=Registry)
    async def create_device(body: DeviceWrite, authorization: str | None = Header(default=None, alias="Authorization")) -> Registry:
        return await relay(authorization, method="POST", resource="devices", payload=body.model_dump(mode="json"))

    @router.put("/devices/{device_id}", response_model=Registry)
    async def update_device(device_id: str, body: DeviceWrite, authorization: str | None = Header(default=None, alias="Authorization")) -> Registry:
        return await relay(authorization, method="PUT", resource=f"devices/{quote(device_id, safe='')}", payload=body.model_dump(mode="json"))

    @router.delete("/devices/{device_id}", response_model=Registry)
    async def delete_device(device_id: str, expected_revision: int = Query(ge=0), authorization: str | None = Header(default=None, alias="Authorization")) -> Registry:
        return await relay(authorization, method="DELETE", resource=f"devices/{quote(device_id, safe='')}", expected_revision=expected_revision)

    @router.post("/scenes", response_model=Registry)
    async def create_scene(body: SceneWrite, authorization: str | None = Header(default=None, alias="Authorization")) -> Registry:
        return await relay(authorization, method="POST", resource="scenes", payload=body.model_dump(mode="json"))

    @router.put("/scenes/{scene_id}", response_model=Registry)
    async def update_scene(scene_id: str, body: SceneWrite, authorization: str | None = Header(default=None, alias="Authorization")) -> Registry:
        return await relay(authorization, method="PUT", resource=f"scenes/{quote(scene_id, safe='')}", payload=body.model_dump(mode="json"))

    @router.delete("/scenes/{scene_id}", response_model=Registry)
    async def delete_scene(scene_id: str, expected_revision: int = Query(ge=0), authorization: str | None = Header(default=None, alias="Authorization")) -> Registry:
        return await relay(authorization, method="DELETE", resource=f"scenes/{quote(scene_id, safe='')}", expected_revision=expected_revision)

    @router.put("/placements/{device_ref}", response_model=Registry)
    async def set_placement(device_ref: str, body: PlacementWrite, authorization: str | None = Header(default=None, alias="Authorization")) -> Registry:
        return await relay(authorization, method="PUT", resource=f"placements/{quote(device_ref, safe='')}", payload=body.model_dump())

    @router.delete("/placements/{device_ref}", response_model=Registry)
    async def clear_placement(device_ref: str, expected_revision: int = Query(ge=0), authorization: str | None = Header(default=None, alias="Authorization")) -> Registry:
        return await relay(authorization, method="DELETE", resource=f"placements/{quote(device_ref, safe='')}", expected_revision=expected_revision)

    app.include_router(router)
