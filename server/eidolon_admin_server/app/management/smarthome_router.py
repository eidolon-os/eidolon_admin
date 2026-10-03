"""Internal Owner-scoped smart-home registry bridge to System Data."""

from __future__ import annotations

from urllib.parse import quote

from eidolon_sdk.biz.smarthome import Area, Device, Registry, Scene
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from eidolon_admin_server.app.service_auth import require_local_api_credential

router = APIRouter(
    prefix="/internal/v1/management/smarthome",
    tags=["management-internal"],
    dependencies=[Depends(require_local_api_credential)],
)


class RegistryWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)


class AreaWrite(RegistryWrite):
    area: Area


class DeviceWrite(RegistryWrite):
    device: Device


class SceneWrite(RegistryWrite):
    scene: Scene


class PlacementWrite(RegistryWrite):
    area_id: str = Field(min_length=1, max_length=128)


async def _registry(
    request: Request,
    owner_id: str,
    *,
    method: str = "GET",
    resource: str = "registry",
    payload: dict | None = None,
    expected_revision: int | None = None,
) -> Registry:
    return await request.app.state.control_plane.workspace.smarthome_registry(
        owner_id,
        method=method,
        resource=resource,
        payload=payload,
        expected_revision=expected_revision,
    )


@router.get("/registry", response_model=Registry)
async def get_registry(request: Request, owner_id: str) -> Registry:
    return await _registry(request, owner_id)


@router.post("/samples/apartment", response_model=Registry)
async def load_apartment(request: Request, owner_id: str, body: RegistryWrite) -> Registry:
    return await _registry(request, owner_id, method="POST", resource="samples/apartment", payload=body.model_dump())


@router.post("/areas", response_model=Registry)
async def create_area(request: Request, owner_id: str, body: AreaWrite) -> Registry:
    return await _registry(request, owner_id, method="POST", resource="areas", payload=body.model_dump(mode="json"))


@router.put("/areas/{area_id}", response_model=Registry)
async def update_area(request: Request, owner_id: str, area_id: str, body: AreaWrite) -> Registry:
    return await _registry(request, owner_id, method="PUT", resource=f"areas/{quote(area_id, safe='')}", payload=body.model_dump(mode="json"))


@router.delete("/areas/{area_id}", response_model=Registry)
async def delete_area(request: Request, owner_id: str, area_id: str, expected_revision: int = Query(ge=0)) -> Registry:
    return await _registry(request, owner_id, method="DELETE", resource=f"areas/{quote(area_id, safe='')}", expected_revision=expected_revision)


@router.post("/devices", response_model=Registry)
async def create_device(request: Request, owner_id: str, body: DeviceWrite) -> Registry:
    return await _registry(request, owner_id, method="POST", resource="devices", payload=body.model_dump(mode="json"))


@router.put("/devices/{device_id}", response_model=Registry)
async def update_device(request: Request, owner_id: str, device_id: str, body: DeviceWrite) -> Registry:
    return await _registry(request, owner_id, method="PUT", resource=f"devices/{quote(device_id, safe='')}", payload=body.model_dump(mode="json"))


@router.delete("/devices/{device_id}", response_model=Registry)
async def delete_device(request: Request, owner_id: str, device_id: str, expected_revision: int = Query(ge=0)) -> Registry:
    return await _registry(request, owner_id, method="DELETE", resource=f"devices/{quote(device_id, safe='')}", expected_revision=expected_revision)


@router.post("/scenes", response_model=Registry)
async def create_scene(request: Request, owner_id: str, body: SceneWrite) -> Registry:
    return await _registry(request, owner_id, method="POST", resource="scenes", payload=body.model_dump(mode="json"))


@router.put("/scenes/{scene_id}", response_model=Registry)
async def update_scene(request: Request, owner_id: str, scene_id: str, body: SceneWrite) -> Registry:
    return await _registry(request, owner_id, method="PUT", resource=f"scenes/{quote(scene_id, safe='')}", payload=body.model_dump(mode="json"))


@router.delete("/scenes/{scene_id}", response_model=Registry)
async def delete_scene(request: Request, owner_id: str, scene_id: str, expected_revision: int = Query(ge=0)) -> Registry:
    return await _registry(request, owner_id, method="DELETE", resource=f"scenes/{quote(scene_id, safe='')}", expected_revision=expected_revision)


@router.put("/placements/{device_ref}", response_model=Registry)
async def set_placement(request: Request, owner_id: str, device_ref: str, body: PlacementWrite) -> Registry:
    return await _registry(request, owner_id, method="PUT", resource=f"placements/{quote(device_ref, safe='')}", payload=body.model_dump())


@router.delete("/placements/{device_ref}", response_model=Registry)
async def clear_placement(request: Request, owner_id: str, device_ref: str, expected_revision: int = Query(ge=0)) -> Registry:
    return await _registry(request, owner_id, method="DELETE", resource=f"placements/{quote(device_ref, safe='')}", expected_revision=expected_revision)


# --- Provider accounts: relayed to Hub, which holds the adapters and the vault -----


class AccountBind(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(min_length=1, max_length=32)
    account_id: str | None = Field(default=None, min_length=1, max_length=128)
    fields: dict[str, str] = Field(default_factory=dict)


def _smarthome(request: Request):
    client = request.app.state.control_plane.smarthome
    if client is None:
        from eidolon_admin_server.app.control_plane.errors import AuthorityFailure

        raise AuthorityFailure("hub", "unauthorized", "Hub smart-home credential was not configured", 503)
    return client


@router.get("/providers")
async def providers(request: Request, owner_id: str) -> dict:
    return await _smarthome(request).call(owner_id, "providers")


@router.get("/accounts")
async def accounts(request: Request, owner_id: str) -> dict:
    return await _smarthome(request).call(owner_id, "accounts")


@router.post("/accounts/bind")
async def bind_account(request: Request, owner_id: str, body: AccountBind) -> dict:
    return await _smarthome(request).call(owner_id, "accounts/bind", body.model_dump(mode="json"))


@router.post("/accounts/{account_id}/unbind")
async def unbind_account(request: Request, owner_id: str, account_id: str) -> dict:
    return await _smarthome(request).call(owner_id, f"accounts/{quote(account_id, safe='')}/unbind")


@router.post("/accounts/{account_id}/sync")
async def sync_account(request: Request, owner_id: str, account_id: str) -> dict:
    return await _smarthome(request).call(owner_id, f"accounts/{quote(account_id, safe='')}/sync")


@router.get("/snapshot")
async def snapshot(request: Request, owner_id: str) -> dict:
    return await _smarthome(request).call(owner_id, "snapshot")

