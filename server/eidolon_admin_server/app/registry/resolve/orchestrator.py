"""Resolve device-bound runtime identity from eidolon_data.

Agent runtime sessions are bound to an explicit device. The resolved
context is the minimal envelope channel needs to ask eidolon_agent for a
companion runtime token: owner, companion, device, memory realm, genome.
"""
from __future__ import annotations

from ..schemas.resolve import ResolvedContext


# ---- exceptions ------------------------------------------------------------


class ResolveError(Exception):
    """Base — router maps via ``status_code``."""

    status_code: int = 500


class ResolveDeviceNotBound(ResolveError):
    """Device exists in hub but has no binding row in admin's registry DB.
    Returns 412 — caller must POST to /api/devices/{id}/bind first."""

    status_code = 412


class ResolveDeviceUnavailable(ResolveError):
    """Device exists but is disabled or not approved."""

    status_code = 412


class ResolveError404(ResolveError):
    """Referenced owner or companion does not exist."""

    status_code = 404


class ResolveUpstreamDown(ResolveError):
    """eidolon_data is unavailable or not configured."""

    status_code = 503


# ---- orchestrator ----------------------------------------------------------


class ResolveOrchestrator:
    def __init__(self, *, data_store) -> None:
        self._data_store = data_store

    async def resolve_device(self, device_id: str) -> ResolvedContext:
        """Resolve a device to its runtime identity envelope.

        Failure modes:
          - device missing/unclaimed/unbound → 412
          - companion missing → 404
          - data store unavailable → 503
        """
        if self._data_store is None:
            raise ResolveUpstreamDown("eidolon_data store is not configured")
        device = await self._data_store.devices.get_device(device_id)
        if device is None:
            raise ResolveDeviceNotBound(
                f"device {device_id!r} is not registered in eidolon_data"
            )
        if device.owner_id is None:
            raise ResolveDeviceNotBound(f"device {device_id!r} is not claimed")
        if device.status in {"disabled", "revoked"}:
            raise ResolveDeviceUnavailable(f"device {device_id!r} is {device.status}")
        if not device.bound_companion_id:
            raise ResolveDeviceNotBound(f"device {device_id!r} is not bound to a companion")

        companion = await self._data_store.companions.get(device.bound_companion_id)
        if companion is None:
            raise ResolveError404(f"companion {device.bound_companion_id!r} not found")
        if companion.owner_id != device.owner_id:
            raise ResolveDeviceUnavailable(
                f"device {device_id!r} is bound outside owner {device.owner_id!r}"
            )

        memory_realm_id = companion.default_memory_realm_id or ""
        genome_id = companion.current_genome_id or ""
        if not memory_realm_id:
            raise ResolveDeviceUnavailable(
                f"companion {companion.companion_id!r} has no default memory realm"
            )
        if not genome_id:
            raise ResolveDeviceUnavailable(
                f"companion {companion.companion_id!r} has no current genome"
            )
        return ResolvedContext(
            owner_id=device.owner_id,
            companion_id=companion.companion_id,
            device_id=device_id,
            memory_realm_id=memory_realm_id,
            genome_id=genome_id,
            interaction_mode=device.interaction_mode,
        )
