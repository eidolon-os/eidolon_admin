"""Repository layer for Devices.

Two stores (same pattern as Users / Agents):

  - :class:`HubDeviceClient` — HTTP to hub's ``/api/admin/devices*``.
    Hub owns the device fact (id, name, approved, last_seen) — admin
    reads and mutates facts only through hub's REST surface.

  - :class:`DeviceBindingRepository` — admin's registry SQLite table.
    Stores ``{agent_id, bound_at}``
    keyed by device_id. This is the editorial decision the operator
    makes in admin UI; doesn't belong in hub (hub has no agent concept).
"""
from __future__ import annotations

import logging
from typing import Any

from eidolon_sdk.core.http import (
    ServiceHTTPClient,
)
from eidolon_sdk.biz.registry.models import DeviceBindingRecord

from ..schemas.device import DeviceBinding

logger = logging.getLogger(__name__)


# ===== HTTP client to hub ===================================================


class HubDeviceClient(ServiceHTTPClient):
    """Hub's ``/api/admin/devices*`` (read + approve + unregister)."""

    async def list_devices(self) -> list[dict[str, Any]]:
        """Returns the ``devices`` list from hub's envelope. Hub returns
        ``{"devices": [...]}`` — we unwrap so callers see a flat list."""
        r = await self._request("GET", "/api/admin/devices")
        body = r.json()
        return body.get("devices", [])

    async def get_discovery_status(self) -> dict[str, Any]:
        r = await self._request("GET", "/api/admin/discovery")
        return r.json()

    async def get_device(self, device_id: str) -> dict[str, Any]:
        r = await self._request("GET", f"/api/admin/devices/{device_id}")
        return r.json()

    async def approve_device(self, device_id: str) -> dict[str, Any]:
        r = await self._request(
            "POST", f"/api/admin/devices/{device_id}/approve"
        )
        return r.json()

    async def set_device_enabled(
        self, device_id: str, *, enabled: bool
    ) -> dict[str, Any]:
        r = await self._request(
            "POST",
            f"/api/admin/devices/{device_id}/enable",
            params={"enabled": enabled},
        )
        return r.json()

    async def send_config_refresh(self, device_id: str) -> dict[str, Any]:
        r = await self._request(
            "POST",
            f"/api/admin/devices/{device_id}/commands",
            json={
                "topic": "eidolon.control",
                "op": "config.refresh",
                "payload": {"reason": "admin_state_changed"},
                "ttl_ms": 30000,
                "qos": "ack",
            },
        )
        return r.json()

    async def send_room_join(self, device_id: str) -> dict[str, Any]:
        r = await self._request(
            "POST",
            f"/api/admin/devices/{device_id}/commands",
            json={
                "topic": "eidolon.control",
                "op": "room.join",
                "payload": {"reason": "admin_wake"},
                "ttl_ms": 30000,
                "qos": "ack",
                "priority": "high",
            },
        )
        return r.json()

    async def unregister_device(self, device_id: str) -> dict[str, Any]:
        """Returns hub's envelope (existed + presence_cleared flags)."""
        r = await self._request("DELETE", f"/api/admin/devices/{device_id}")
        return r.json()


# ===== admin's own binding registry ========================================


class DeviceBindingRepository:
    """SQLite-backed store for device → agent bindings.

    A device with no binding row in this table is "approved but not
    configured" — voice/chat sessions for it must hard-reject (the
    orchestrator + /api/resolve enforce this). Different from the old
    Phase 25 model where ``DeviceBinding`` was a richer object with
    ``agent_ids[]`` + ``active_agent_id``; the new model is just a
    flat pointer.
    """

    def __init__(self, repository: Any) -> None:
        self._repo = repository

    async def get(self, device_id: str) -> DeviceBinding | None:
        record = await self._repo.get(device_id)
        if record is None:
            return None
        try:
            return DeviceBinding(
                agent_id=record.agent_id,
                bound_at=record.bound_at,
                interaction_mode=record.interaction_mode,
            )
        except Exception:
            logger.exception("devices: malformed registry entry %s", device_id)
            return None

    async def put(self, device_id: str, binding: DeviceBinding) -> None:
        await self._repo.put(
            DeviceBindingRecord(
                device_id=device_id,
                agent_id=binding.agent_id,
                bound_at=binding.bound_at.isoformat(),
                interaction_mode=binding.interaction_mode,
            )
        )

    async def delete(self, device_id: str) -> None:
        """Idempotent."""
        await self._repo.delete(device_id)

    async def list_all(self) -> dict[str, DeviceBinding]:
        """Return the full device_id → DeviceBinding map.

        Used by the orchestrator's list path (it joins hub's device
        records × admin's bindings × agent metadata for the full view).
        """
        out: dict[str, DeviceBinding] = {}
        for record in (await self._repo.list_all()).values():
            try:
                out[record.device_id] = DeviceBinding(
                    agent_id=record.agent_id,
                    bound_at=record.bound_at,
                    interaction_mode=record.interaction_mode,
                )
            except Exception:
                logger.exception(
                    "devices: malformed registry entry %s", record.device_id
                )
        return out

    async def list_by_agent(self, agent_id: str) -> list[str]:
        """Devices currently bound to this agent. Used by the Agents
        cascade: when an agent is deleted, list its devices and unbind."""
        return await self._repo.list_by_agent(agent_id)
