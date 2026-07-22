"""Hub runtime client used by owner-scoped data flows.

Hub owns device runtime facts: whether a physical device is approved, online,
and reachable for a control command. Owner/companion binding stays in
``eidolon_data``; this client deliberately has no owner, companion, or agent
concepts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx


class HubRuntimeUnavailable(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class HubRuntimeDevice:
    device_id: str
    name: str = ""
    kind: str = "unknown"
    enabled: bool = True
    paired: bool = False
    approved: bool = False
    approved_at: datetime | None = None
    last_seen: datetime | None = None
    last_ip: str = ""
    status: str = "offline"
    room_name: str = ""
    participant_sid: str = ""
    missed_probes: int = 0


class HubDeviceRuntimeClient:
    def __init__(self, http_client: httpx.AsyncClient, base_url: str) -> None:
        self._http = http_client
        self._base_url = base_url.rstrip("/")

    async def list_devices(self) -> list[HubRuntimeDevice]:
        body = await self._request_json("GET", "/api/admin/devices")
        rows = body.get("devices") if isinstance(body, dict) else None
        if not isinstance(rows, list):
            return []
        return [_device_from_json(row) for row in rows if isinstance(row, dict)]

    async def get_device(self, device_id: str) -> HubRuntimeDevice:
        body = await self._request_json("GET", f"/api/admin/devices/{device_id}")
        if not isinstance(body, dict):
            raise HubRuntimeUnavailable("invalid Hub device response")
        return _device_from_json(body)

    async def identify_device(self, device_id: str) -> dict[str, Any]:
        body = await self._request_json(
            "POST",
            f"/api/admin/devices/{device_id}/commands",
            json={
                "op": "device.identify",
                "payload": {"reason": "owner_admin_identify"},
                "ttl_ms": 30_000,
                "qos": "ack",
            },
        )
        return body if isinstance(body, dict) else {}

    async def wiggle_device(self, device_id: str) -> dict[str, Any]:
        # "动一动": nudge the body into its awake / back-at-desk reaction
        # (sound + head sway + RGB + expression). The body.presence.set command
        # contract now lives in the Hub's BodyPresenceDispatcher (shared with
        # the guard owner-presence reflex), so this only triggers the endpoint
        # rather than hand-building the payload.
        body = await self._request_json(
            "POST",
            f"/api/admin/devices/{device_id}/wiggle",
        )
        return body if isinstance(body, dict) else {}

    async def refresh_device_config(self, device_id: str) -> dict[str, Any]:
        body = await self._request_json(
            "POST",
            f"/api/admin/devices/{device_id}/commands",
            json={
                "op": "config.refresh",
                "payload": {"reason": "owner_binding_changed"},
                "ttl_ms": 30_000,
                "qos": "ack",
            },
        )
        return body if isinstance(body, dict) else {}

    async def _request_json(self, method: str, path: str, **kwargs: Any) -> Any:
        if not self._base_url:
            raise HubRuntimeUnavailable("Hub service URL is not configured", status_code=503)
        try:
            response = await self._http.request(method, f"{self._base_url}{path}", **kwargs)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = _response_detail(exc.response)
            raise HubRuntimeUnavailable(detail, status_code=exc.response.status_code) from exc
        except httpx.HTTPError as exc:
            raise HubRuntimeUnavailable(str(exc), status_code=503) from exc
        return response.json()


def _device_from_json(row: dict[str, Any]) -> HubRuntimeDevice:
    return HubRuntimeDevice(
        device_id=str(row.get("device_id") or ""),
        name=str(row.get("name") or ""),
        kind=str(row.get("kind") or "unknown"),
        enabled=bool(row.get("enabled", True)),
        paired=bool(row.get("paired", False)),
        approved=bool(row.get("approved", False)),
        approved_at=_parse_datetime(row.get("approved_at")),
        last_seen=_parse_datetime(row.get("last_seen")),
        last_ip=str(row.get("last_ip") or ""),
        status=str(row.get("status") or "offline"),
        room_name=str(row.get("room_name") or ""),
        participant_sid=str(row.get("participant_sid") or ""),
        missed_probes=int(row.get("missed_probes") or 0),
    )


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _response_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text or response.reason_phrase
    if isinstance(body, dict):
        detail = body.get("detail") or body.get("message")
        if detail:
            return str(detail)
    return response.reason_phrase
