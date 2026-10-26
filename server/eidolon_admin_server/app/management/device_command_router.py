"""Device capability commands, carried to the Provider that owns delivery.

A forwarder, and that is the whole design. The Channel Provider already holds
the body's channel, validates that the capability was declared, checks the
payload against that declaration and waits for a terminal receipt; it is the
only process that can. Nothing here re-decides any of it.

What this hop adds is the credential. The Provider answers loopback callers it
trusts, and the Local API — the process a phone reaches — holds no Provider
token. So the command travels to the process that does, and the answer travels
back unchanged, including a refusal's own wording and status.
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from eidolon_admin_server.app.gateway.proxy import upstream_auth_headers
from eidolon_admin_server.app.service_auth import require_local_api_credential

router = APIRouter(
    prefix="/internal/v1/management",
    tags=["management-internal"],
    dependencies=[Depends(require_local_api_credential)],
)


class DeviceCommandBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner_id: str = Field(min_length=1, max_length=128)
    device_id: str = Field(min_length=1, max_length=128)
    op: str = Field(min_length=3, max_length=128)
    capability_version: int = Field(ge=1, le=65535)
    payload: dict = Field(default_factory=dict)
    #: Chosen by the caller and stable across retries of one logical command, so
    #: a Provider that already answered answers again instead of acting twice.
    idempotency_key: str = Field(min_length=1, max_length=128)
    #: Empty when the command did not come from a Companion's session — the
    #: Owner's own app addresses a body directly.
    companion_id: str = Field(default="", max_length=128)


@router.post("/device-commands")
async def deliver_device_command(request: Request, body: DeviceCommandBody) -> dict:
    service = request.app.state.registry.get("channel-provider")
    if service is None or not service.base_url:
        raise HTTPException(503, "设备能力服务未配置")
    url = (
        f"{service.base_url.rstrip('/')}{(service.upstream_prefix or '').rstrip('/')}"
        "/v1/device-channels/commands"
    )
    try:
        response = await request.app.state.http_client.post(
            url,
            json={
                "owner_id": body.owner_id,
                "companion_id": body.companion_id,
                "device_id": body.device_id,
                "op": body.op,
                "capability_version": body.capability_version,
                "payload": body.payload,
                "idempotency_key": body.idempotency_key,
            },
            headers=upstream_auth_headers(service),
            timeout=35.0,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(503, "设备能力服务暂时不可用") from exc

    if response.status_code != 200:
        # A refusal is the Provider's to word: it knows which capability was
        # missing, whose device this is, and whether a receipt timed out.
        # Restating it here would be a second, worse sentence about the same
        # fact, so the status and detail travel as they arrived.
        detail: object
        try:
            payload = response.json()
            detail = payload.get("detail") if isinstance(payload, dict) else None
        except ValueError:
            detail = None
        raise HTTPException(
            status_code=response.status_code
            if 400 <= response.status_code < 500
            else 503,
            detail=detail or "设备能力命令被拒绝",
        )

    try:
        answer = response.json()
    except ValueError as exc:
        raise HTTPException(503, "设备能力服务答复无法解析") from exc
    if not isinstance(answer, dict):
        raise HTTPException(503, "设备能力服务答复无法解析")
    return answer
