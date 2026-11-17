"""A device's settings, carried to the Channel Provider that holds its channel.

A forwarder. The Provider owns everything that decides an answer: whether the
device is this Owner's, whether the capability is an installed contract the
Owner's app may use, whether the device declared it, whether the arguments fit,
and what the device then did. This hop adds the credential and relays the
answer — a refusal with the Provider's own code and sentence — unchanged.
``DeviceOffers`` reads the same way, for the tools page that composes it.

The Owner is not chosen here either. Local API passes the one bound to the
Controller session it verified; nothing on this plane lets a client name one.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx
from eidolon_sdk.biz.tools import DeviceToolOffer
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from eidolon_admin_server.app.gateway.proxy import upstream_auth_headers
from eidolon_admin_server.app.service_auth import require_local_api_credential

router = APIRouter(
    prefix="/internal/v1/management",
    tags=["management-internal"],
    dependencies=[Depends(require_local_api_credential)],
)


class DeviceCapabilityInvocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner_id: str = Field(min_length=1, max_length=128)
    request_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    arguments: dict[str, Any] = Field(default_factory=dict)


def _provider_url(request: Request, path: str) -> tuple[str, dict[str, str]]:
    service = request.app.state.registry.get("channel-provider")
    if service is None or not service.base_url:
        raise HTTPException(
            503, {"code": "CHANNEL_PROVIDER_UNAVAILABLE", "detail": "设备通道服务未配置"}
        )
    prefix = (service.upstream_prefix or "").rstrip("/")
    return f"{service.base_url.rstrip('/')}{prefix}{path}", upstream_auth_headers(service)


def _relayed(response: httpx.Response) -> dict:
    """The Provider's answer, or its refusal in the shape Local API relays.

    A 4xx keeps its status, code and sentence: the Provider knows whose device
    this is and which capability was missing, and restating that here would be
    a second, worse sentence about the same fact. Anything else from upstream
    means it could not answer, which is a 503 and not the client's fault.
    """

    try:
        body = response.json()
    except ValueError:
        body = None
    if response.status_code == 200 and isinstance(body, dict):
        return body
    if 400 <= response.status_code < 500 and isinstance(body, dict):
        raise HTTPException(
            response.status_code,
            {"code": str(body.get("code") or "REFUSED"), "detail": str(body.get("detail") or "")},
        )
    raise HTTPException(
        503,
        {"code": "CHANNEL_PROVIDER_UNAVAILABLE",
         "detail": f"设备通道服务答复异常（{response.status_code}）"},
    )


async def _read(request: Request, device_id: str, leaf: str, owner_id: str) -> httpx.Response:
    """One of the Provider's reads about a device, asked for the Owner Local API named."""

    url, headers = _provider_url(
        request, f"/v1/device-channels/{quote(device_id, safe='')}/{leaf}"
    )
    try:
        return await request.app.state.http_client.get(
            url, params={"owner_id": owner_id}, headers=headers, timeout=10.0
        )
    except httpx.HTTPError as exc:
        raise HTTPException(
            503, {"code": "CHANNEL_PROVIDER_UNAVAILABLE", "detail": "设备通道服务暂时不可用"}
        ) from exc


@router.get("/devices/{device_id}/properties")
async def device_properties(request: Request, device_id: str, owner_id: str) -> dict:
    return _relayed(await _read(request, device_id, "properties", owner_id))


class DeviceOffers:
    """What a device's channel offers a session, read from the Provider that granted it.

    Not a route of its own: the tools page composes it with the Owner's
    switches, and the device facts in that answer are the Provider's, read
    rather than re-derived here.
    """

    def __init__(self, request: Request) -> None:
        self._request = request

    async def offer(self, *, owner_id: str, device_id: str) -> DeviceToolOffer | None:
        """The offer, or ``None`` when this device has no channel on this Host yet.

        Any other refusal (another Owner's device) is relayed like the reads
        above, and an answer that cannot be read is the Provider being unable
        to answer, never the client's fault.
        """

        response = await _read(self._request, device_id, "offer", owner_id)
        if response.status_code == 404:
            return None
        try:
            return DeviceToolOffer.model_validate(_relayed(response))
        except ValidationError as exc:
            raise HTTPException(
                503,
                {"code": "CHANNEL_PROVIDER_UNAVAILABLE", "detail": "设备通道服务的答复不符合约定"},
            ) from exc


@router.post("/devices/{device_id}/capabilities/{capability}")
async def invoke_device_capability(
    request: Request, device_id: str, capability: str, body: DeviceCapabilityInvocation
) -> dict:
    url, headers = _provider_url(
        request,
        f"/v1/device-channels/{quote(device_id, safe='')}/capabilities/"
        f"{quote(capability, safe='')}",
    )
    try:
        response = await request.app.state.http_client.post(
            url, json=body.model_dump(mode="json"), headers=headers, timeout=20.0
        )
    except httpx.HTTPError as exc:
        raise HTTPException(
            503, {"code": "CHANNEL_PROVIDER_UNAVAILABLE", "detail": "设备通道服务暂时不可用"}
        ) from exc
    return _relayed(response)
