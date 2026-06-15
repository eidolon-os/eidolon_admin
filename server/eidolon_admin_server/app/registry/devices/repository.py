"""Repository layer for Devices.

Two stores (same pattern as Users / Agents):

  - :class:`HubDeviceClient` — HTTP to hub's ``/api/admin/devices*``.
    Hub owns the device fact (id, name, approved, last_seen) — admin
    never touches devices.json directly.

  - :class:`DeviceBindingRepository` — admin's own KV bucket
    ``eidolon_admin_device_bindings``. Stores ``{agent_id, bound_at}``
    keyed by device_id. This is the editorial decision the operator
    makes in admin UI; doesn't belong in hub (hub has no agent concept).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from ...nats_kv import KVClient, from_json_bytes, to_json_bytes
from .._shared import (
    SubProjectHTTPClient,
    SubProjectUnreachable,
    SubProjectUpstreamError,
)
from ..buckets import DEVICE_BINDINGS_BUCKET
from ..keys import decode_device_binding_key, device_binding_key, legacy_device_binding_key
from ..schemas.device import DeviceBinding

logger = logging.getLogger(__name__)


# Backwards-compatible aliases for clearer in-module names; the shared
# exceptions ARE these classes.
DeviceHubUnreachable = SubProjectUnreachable
DeviceHubUpstreamError = SubProjectUpstreamError


# ===== HTTP client to hub ===================================================


class HubDeviceClient(SubProjectHTTPClient):
    """Hub's ``/api/admin/devices*`` (read + approve + unregister)."""

    async def list_devices(self) -> list[dict[str, Any]]:
        """Returns the ``devices`` list from hub's envelope. Hub returns
        ``{"devices": [...]}`` — we unwrap so callers see a flat list."""
        r = await self._request("GET", "/api/admin/devices")
        body = r.json()
        return body.get("devices", [])

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


# ===== admin's own binding KV ==============================================


class DeviceBindingRepository:
    """KV-backed store for device → agent bindings.

    A device with no binding row in this bucket is "approved but not
    configured" — voice/chat sessions for it must hard-reject (the
    orchestrator + /api/resolve enforce this). Different from the old
    Phase 25 model where ``DeviceBinding`` was a richer object with
    ``agent_ids[]`` + ``active_agent_id``; the new model is just a
    flat pointer.
    """

    def __init__(self, kv: KVClient) -> None:
        self._kv = kv

    async def get(self, device_id: str) -> DeviceBinding | None:
        raw = await self._kv.get(DEVICE_BINDINGS_BUCKET.name, device_binding_key(device_id))
        if raw is None:
            legacy_key = legacy_device_binding_key(device_id)
            if legacy_key and legacy_key != device_binding_key(device_id):
                raw = await self._kv.get(DEVICE_BINDINGS_BUCKET.name, legacy_key)
        if raw is None:
            return None
        try:
            return DeviceBinding.model_validate(from_json_bytes(raw))
        except Exception:
            logger.exception("devices: malformed KV entry %s", device_id)
            return None

    async def put(self, device_id: str, binding: DeviceBinding) -> None:
        await self._kv.put(
            DEVICE_BINDINGS_BUCKET.name,
            device_binding_key(device_id),
            to_json_bytes(binding.model_dump(mode="json")),
        )

    async def delete(self, device_id: str) -> None:
        """Idempotent."""
        await self._kv.delete(DEVICE_BINDINGS_BUCKET.name, device_binding_key(device_id))
        legacy_key = legacy_device_binding_key(device_id)
        if legacy_key and legacy_key != device_binding_key(device_id):
            await self._kv.delete(DEVICE_BINDINGS_BUCKET.name, legacy_key)

    async def list_all(self) -> dict[str, DeviceBinding]:
        """Return the full device_id → DeviceBinding map.

        Used by the orchestrator's list path (it joins hub's device
        records × admin's bindings × agent metadata for the full view).
        """
        keys = await self._kv.list_keys(
            DEVICE_BINDINGS_BUCKET.name, prefix="device."
        )
        out: dict[str, DeviceBinding] = {}
        for key in keys:
            raw = await self._kv.get(DEVICE_BINDINGS_BUCKET.name, key)
            if raw is None:
                continue
            device_id = decode_device_binding_key(key)
            if not device_id:
                logger.warning("devices: ignoring malformed binding key %s", key)
                continue
            try:
                out[device_id] = DeviceBinding.model_validate(from_json_bytes(raw))
            except Exception:
                logger.exception("devices: malformed KV entry at key %s", key)
        return out

    async def list_by_agent(self, agent_id: str) -> list[str]:
        """Devices currently bound to this agent. Used by the Agents
        cascade: when an agent is deleted, list its devices and unbind."""
        all_bindings = await self.list_all()
        return [
            device_id for device_id, b in all_bindings.items()
            if b.agent_id == agent_id
        ]
