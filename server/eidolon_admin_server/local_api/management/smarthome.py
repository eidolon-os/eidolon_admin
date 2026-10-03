"""Controller-authenticated mobile routes for one Owner's smart home."""

from __future__ import annotations

from urllib.parse import quote

from eidolon_sdk.biz.smarthome import AccountSchema, ProviderAccount, Registry
from fastapi import APIRouter, Header, Query
from pydantic import BaseModel, ConfigDict, Field

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

    # --- Provider accounts (bind an ecosystem, import its devices) ---------------

    async def relay_accounts(authorization, *, method="GET", resource, payload=None) -> dict:
        owner_id = await authenticated_owner(authorization)
        try:
            return await backend.smarthome_accounts(
                owner_id=owner_id, method=method, resource=resource, payload=payload
            )
        except ManagementBackendError as exc:
            raise _refused(exc) from exc

    @router.get("/providers", response_model=ProviderList)
    async def list_providers(authorization: str | None = Header(default=None, alias="Authorization")) -> ProviderList:
        return ProviderList.model_validate(await relay_accounts(authorization, resource="providers"))

    @router.get("/accounts", response_model=AccountList)
    async def list_accounts(authorization: str | None = Header(default=None, alias="Authorization")) -> AccountList:
        return AccountList.model_validate(await relay_accounts(authorization, resource="accounts"))

    @router.post("/accounts/bind", response_model=ProviderAccount)
    async def bind_account(body: AccountBind, authorization: str | None = Header(default=None, alias="Authorization")) -> ProviderAccount:
        return ProviderAccount.model_validate(
            await relay_accounts(authorization, method="POST", resource="accounts/bind", payload=body.model_dump(mode="json"))
        )

    @router.post("/accounts/{account_id}/unbind", response_model=AccountRemoved)
    async def unbind_account(account_id: str, authorization: str | None = Header(default=None, alias="Authorization")) -> AccountRemoved:
        return AccountRemoved.model_validate(
            await relay_accounts(authorization, method="POST", resource=f"accounts/{quote(account_id, safe='')}/unbind")
        )

    @router.post("/accounts/{account_id}/sync", response_model=SyncReport)
    async def sync_account(account_id: str, authorization: str | None = Header(default=None, alias="Authorization")) -> SyncReport:
        return SyncReport.model_validate(
            await relay_accounts(authorization, method="POST", resource=f"accounts/{quote(account_id, safe='')}/sync")
        )

    @router.get("/snapshot", response_model=HomeSnapshotView)
    async def home_snapshot(authorization: str | None = Header(default=None, alias="Authorization")) -> HomeSnapshotView:
        return HomeSnapshotView.model_validate(await relay_accounts(authorization, resource="snapshot"))

    app.include_router(router)


class ProviderList(BaseModel):
    model_config = ConfigDict(extra="forbid")
    providers: list[AccountSchema]


class AccountList(BaseModel):
    model_config = ConfigDict(extra="forbid")
    accounts: list[ProviderAccount]


class AccountBind(BaseModel):
    """What the phone sends to bind an ecosystem account; the fields follow the Provider's AccountSchema."""

    model_config = ConfigDict(extra="forbid")
    kind: str = Field(min_length=1, max_length=32)
    account_id: str | None = Field(default=None, min_length=1, max_length=128)
    fields: dict[str, str] = Field(default_factory=dict)


class AccountRemoved(BaseModel):
    model_config = ConfigDict(extra="forbid")
    removed: str


class SyncSkipped(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ref: str
    reason: str


class SyncReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    added: list[str]
    updated: list[str]
    orphaned: list[str]
    skipped: list[SyncSkipped]
    revision: int


class DeviceStatusView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    online: bool
    state: dict[str, bool | int | float | str | None]


class HomeSnapshotView(BaseModel):
    """The registry with what Hub has observed of each device."""

    model_config = ConfigDict(extra="forbid")
    registry: Registry
    status: dict[str, DeviceStatusView]
