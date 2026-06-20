"""Devices orchestration — bind to existing agents + cascade unbind.

Three cross-project responsibilities:

  - **list/get**: hub authoritative for device facts, admin's KV for
    binding, agent's metadata for the resolved agent label. The
    orchestrator joins all three so the UI sees one composed row.

  - **bind**: validate the agent exists in admin's registry BEFORE
    writing the binding (no orphan-pointer bindings).

  - **unbind**: idempotent; deletes admin's KV entry. Hub fact stays —
    a device can be unbound + still approved (it's idle until rebound).

  - **approve** / **unregister**: proxies to hub's existing endpoints.
    ``unregister`` ALSO cleans admin's binding so the device is gone
    from every side.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from eidolon_sdk.http import ServiceUnavailable, ServiceUpstreamError

from .._shared import unwrap_detail
from ..schemas.device import (
    BindDeviceRequest,
    DeviceBinding,
    DeviceView,
)
from .repository import (
    DeviceBindingRepository,
    HubDeviceClient,
)

logger = logging.getLogger(__name__)


# ---- exceptions ------------------------------------------------------------


class DeviceError(Exception):
    status_code: int = 500


class DeviceNotFound(DeviceError):
    status_code = 404


class DeviceNotApproved(DeviceError):
    """Trying to bind a device that hasn't been approved yet."""

    status_code = 409


class DeviceDisabled(DeviceError):
    """Trying to use a device that has been administratively disabled."""

    status_code = 409


class DeviceBadRequest(DeviceError):
    """Caller's request references something that doesn't exist
    (e.g. binding to a non-existent agent)."""

    status_code = 400


class DeviceAgentMismatch(DeviceError):
    """Operational inconsistency: KV says device bound to agent X,
    but admin's agent registry has no record of X (drift)."""

    status_code = 500


class DeviceHubDown(DeviceError):
    status_code = 503


# ---- helper types ---------------------------------------------------------


# Async callable: (agent_id) → metadata-or-None. Wired by the lifespan
# from the AgentMetadataRepository so the orchestrator can resolve
# binding → agent label without importing AgentOrchestrator directly.
AgentExistsCheck = Callable[[str], Awaitable[Any]]  # returns AgentMetadata | None


class DeviceOrchestrator:
    def __init__(
        self,
        *,
        hub_client: HubDeviceClient,
        binding_repo: DeviceBindingRepository,
        agent_lookup: AgentExistsCheck,
    ) -> None:
        self._hub = hub_client
        self._bindings = binding_repo
        self._agent_lookup = agent_lookup

    # ---- helpers -------------------------------------------------------

    def _map_hub_error(self, exc: ServiceUpstreamError) -> None:
        message = unwrap_detail(exc.message)
        if exc.status_code == 404:
            raise DeviceNotFound(message)
        if exc.status_code == 409:
            raise DeviceBadRequest(message)
        raise DeviceError(f"hub returned {exc.status_code}: {message}")

    async def _notify_config_refresh(self, device_id: str) -> None:
        try:
            await self._hub.send_config_refresh(device_id)
        except Exception as exc:
            logger.info(
                "device_config_refresh_not_sent device_id=%s error=%s",
                device_id,
                exc,
            )

    @staticmethod
    def _hub_record_to_view(
        record: dict[str, Any],
        *,
        binding: DeviceBinding | None,
        resolved_user_id: str | None,
        resolved_template_id: str | None,
    ) -> DeviceView:
        """Compose admin's wire shape from hub's AdminDevice + binding
        + agent metadata (already looked up by the caller)."""
        return DeviceView(
            device_id=record["device_id"],
            name=record.get("name", ""),
            kind=record.get("kind", "unknown"),
            enabled=bool(record.get("enabled", True)),
            approved=bool(record.get("approved")),
            approved_at=_parse_dt_optional(record.get("approved_at")),
            last_seen=_parse_dt_optional(record.get("last_seen")),
            status=record.get("status", "unknown"),
            room_name=record.get("room_name", ""),
            missed_probes=int(record.get("missed_probes") or 0),
            binding=binding,
            resolved_user_id=resolved_user_id,
            resolved_template_id=resolved_template_id,
        )

    # ---- public API ----------------------------------------------------

    async def list_devices(self) -> list[DeviceView]:
        """Joins hub's device list × admin's bindings × agent metadata."""
        try:
            hub_records = await self._hub.list_devices()
        except ServiceUnavailable as exc:
            raise DeviceHubDown(str(exc)) from exc
        except ServiceUpstreamError as exc:
            self._map_hub_error(exc)

        bindings = await self._bindings.list_all()
        out: list[DeviceView] = []
        for record in hub_records:
            device_id = record["device_id"]
            binding = bindings.get(device_id)
            resolved_user = None
            resolved_template = None
            if binding is not None:
                agent_meta = await self._agent_lookup(binding.agent_id)
                if agent_meta is not None:
                    resolved_user = agent_meta.user_id
                    resolved_template = agent_meta.template_id
            out.append(
                self._hub_record_to_view(
                    record,
                    binding=binding,
                    resolved_user_id=resolved_user,
                    resolved_template_id=resolved_template,
                )
            )
        return out

    async def get_device(self, device_id: str) -> DeviceView:
        try:
            record = await self._hub.get_device(device_id)
        except ServiceUnavailable as exc:
            raise DeviceHubDown(str(exc)) from exc
        except ServiceUpstreamError as exc:
            self._map_hub_error(exc)

        binding = await self._bindings.get(device_id)
        resolved_user = None
        resolved_template = None
        if binding is not None:
            agent_meta = await self._agent_lookup(binding.agent_id)
            if agent_meta is not None:
                resolved_user = agent_meta.user_id
                resolved_template = agent_meta.template_id
        return self._hub_record_to_view(
            record,
            binding=binding,
            resolved_user_id=resolved_user,
            resolved_template_id=resolved_template,
        )

    async def approve_device(self, device_id: str) -> DeviceView:
        """Proxy to hub's approve + return updated view."""
        try:
            await self._hub.approve_device(device_id)
        except ServiceUnavailable as exc:
            raise DeviceHubDown(str(exc)) from exc
        except ServiceUpstreamError as exc:
            self._map_hub_error(exc)
        await self._notify_config_refresh(device_id)
        return await self.get_device(device_id)

    async def set_device_enabled(
        self, device_id: str, *, enabled: bool
    ) -> DeviceView:
        """Enable/disable the hub device record and refresh device config.

        This does not delete the device or clear its binding. Disable is a
        reversible operational stop: the device stays visible, but runtime
        actions are rejected until re-enabled.
        """
        try:
            record = await self._hub.set_device_enabled(device_id, enabled=enabled)
        except ServiceUnavailable as exc:
            raise DeviceHubDown(str(exc)) from exc
        except ServiceUpstreamError as exc:
            self._map_hub_error(exc)
        if not enabled:
            logger.info("device_disabled device_id=%s", device_id)
        else:
            logger.info("device_enabled device_id=%s", device_id)
        await self._notify_config_refresh(device_id)
        binding = await self._bindings.get(device_id)
        resolved_user = None
        resolved_template = None
        if binding is not None:
            agent_meta = await self._agent_lookup(binding.agent_id)
            if agent_meta is not None:
                resolved_user = agent_meta.user_id
                resolved_template = agent_meta.template_id
        return self._hub_record_to_view(
            record,
            binding=binding,
            resolved_user_id=resolved_user,
            resolved_template_id=resolved_template,
        )

    async def bind_device(
        self, device_id: str, body: BindDeviceRequest
    ) -> DeviceView:
        """Bind a device to an EXISTING agent. Two validation gates:

          1. device must exist in hub AND be approved
          2. agent_id must exist in admin's registry

        Both gates fire BEFORE the KV write so a failed bind leaves no
        partial state.
        """
        # Gate 1 — device exists + approved
        try:
            record = await self._hub.get_device(device_id)
        except ServiceUnavailable as exc:
            raise DeviceHubDown(str(exc)) from exc
        except ServiceUpstreamError as exc:
            self._map_hub_error(exc)
        if not record.get("enabled", True):
            raise DeviceDisabled(
                f"device {device_id!r} is disabled. Enable it before binding."
            )
        if not record.get("approved"):
            raise DeviceNotApproved(
                f"device {device_id!r} not approved yet. Call POST "
                "/api/devices/{id}/approve first."
            )

        # Gate 2 — agent exists in admin's registry
        agent_meta = await self._agent_lookup(body.agent_id)
        if agent_meta is None:
            raise DeviceBadRequest(
                f"cannot bind: agent {body.agent_id!r} not found in admin "
                "registry. Create the agent first via POST /api/agents."
            )

        # Write binding
        binding = DeviceBinding(
            agent_id=body.agent_id,
            bound_at=datetime.now(timezone.utc),
            interaction_mode=body.interaction_mode,
        )
        await self._bindings.put(device_id, binding)
        logger.info(
            "device_bound device_id=%s agent_id=%s interaction_mode=%s",
            device_id,
            body.agent_id,
            body.interaction_mode,
        )
        await self._notify_config_refresh(device_id)
        return await self.get_device(device_id)

    async def unbind_device(self, device_id: str) -> DeviceView:
        """Idempotent — clears the binding row. Doesn't touch hub's
        device record (the device is still approved, just not configured)."""
        await self._bindings.delete(device_id)
        logger.info("device_unbound device_id=%s", device_id)
        await self._notify_config_refresh(device_id)
        return await self.get_device(device_id)

    async def wake_device(self, device_id: str) -> dict[str, Any]:
        """Ask a standby device to join its voice room via control channel."""
        view = await self.get_device(device_id)
        if not view.enabled:
            raise DeviceDisabled(f"device {device_id!r} is disabled")
        if not view.approved:
            raise DeviceNotApproved(f"device {device_id!r} is not approved")
        if view.binding is None:
            raise DeviceBadRequest(f"device {device_id!r} is not bound to an agent")
        try:
            return await self._hub.send_room_join(device_id)
        except ServiceUnavailable as exc:
            raise DeviceHubDown(str(exc)) from exc
        except ServiceUpstreamError as exc:
            self._map_hub_error(exc)

    async def unregister_device(self, device_id: str) -> dict[str, Any]:
        """Cascade: clean admin's binding + tell hub to forget the device.

        Order matters — we drop admin's binding FIRST so a device that
        re-appears via mDNS after unregister doesn't briefly look bound.
        Hub's unregister is idempotent so duplicate state is fine.
        """
        await self._bindings.delete(device_id)
        try:
            hub_result = await self._hub.unregister_device(device_id)
        except ServiceUnavailable as exc:
            raise DeviceHubDown(str(exc)) from exc
        except ServiceUpstreamError as exc:
            self._map_hub_error(exc)
        # Hub's response is already the right envelope — pass through.
        return hub_result

    # ---- agent cascade hook -------------------------------------------

    async def unbind_all_referring_to(self, agent_id: str) -> list[str]:
        """When an agent is deleted, unbind every device pointing at it.

        Returns the affected device_ids so the caller can log / report
        (matches the user-active-agent cascade pattern in Agents).
        """
        affected = await self._bindings.list_by_agent(agent_id)
        for device_id in affected:
            await self._bindings.delete(device_id)
        if affected:
            logger.info(
                "unbound %d device(s) referring to deleted agent %s: %s",
                len(affected), agent_id, affected,
            )
        return affected


def _parse_dt_optional(value: Any) -> datetime | None:
    """Parse hub's ISO datetime string. Returns None if missing/empty/bad."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt
