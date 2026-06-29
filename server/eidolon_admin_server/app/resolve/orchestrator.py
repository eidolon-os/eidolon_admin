"""Resolve owner/device runtime identity from eidolon_data."""

from __future__ import annotations

from .schemas import ResolvedContext


class ResolveError(Exception):
    """Base class mapped by the router to HTTP status codes."""

    status_code: int = 500


class ResolveNotFound(ResolveError):
    status_code = 404


class ResolvePrecondition(ResolveError):
    status_code = 412


class ResolveUnavailable(ResolveError):
    status_code = 503


class ResolveOrchestrator:
    def __init__(self, *, data_store) -> None:
        self._data_store = data_store

    async def resolve_owner(self, owner_id: str) -> ResolvedContext:
        if self._data_store is None:
            raise ResolveUnavailable("eidolon_data store is not configured")
        owner = await self._data_store.owners.get(owner_id)
        if owner is None:
            raise ResolveNotFound(f"owner {owner_id!r} not found")
        if owner.status != "active":
            raise ResolvePrecondition(f"owner {owner_id!r} is {owner.status}")
        companions = [
            row
            for row in await self._data_store.companions.list_for_owner(owner_id)
            if row.status == "active"
        ]
        if not companions:
            raise ResolvePrecondition(f"owner {owner_id!r} has no active companion")
        ready = [
            row
            for row in companions
            if row.default_memory_realm_id and row.current_genome_id
        ]
        if not ready:
            raise ResolvePrecondition(
                f"owner {owner_id!r} has no companion with memory realm and genome"
            )
        return await self._context_for_companion(ready[0])

    async def resolve_device(self, device_id: str) -> ResolvedContext:
        if self._data_store is None:
            raise ResolveUnavailable("eidolon_data store is not configured")
        device = await self._data_store.devices.get_device(device_id)
        if device is None:
            raise ResolveNotFound(f"device {device_id!r} not found")
        if device.owner_id is None:
            raise ResolvePrecondition(f"device {device_id!r} is not claimed")
        if device.status in {"disabled", "revoked"}:
            raise ResolvePrecondition(f"device {device_id!r} is {device.status}")
        if not device.bound_companion_id:
            raise ResolvePrecondition(
                f"device {device_id!r} is not bound to a companion"
            )
        companion = await self._data_store.companions.get(device.bound_companion_id)
        if companion is None:
            raise ResolveNotFound(f"companion {device.bound_companion_id!r} not found")
        if companion.owner_id != device.owner_id:
            raise ResolvePrecondition(
                f"device {device_id!r} is bound outside owner {device.owner_id!r}"
            )
        context = await self._context_for_companion(companion)
        return context.model_copy(
            update={
                "device_id": device_id,
                "interaction_mode": device.interaction_mode,
            }
        )

    async def _context_for_companion(self, companion) -> ResolvedContext:
        if companion.status != "active":
            raise ResolvePrecondition(
                f"companion {companion.companion_id!r} is {companion.status}"
            )
        memory_realm_id = companion.default_memory_realm_id or ""
        genome_id = companion.current_genome_id or ""
        if not memory_realm_id:
            raise ResolvePrecondition(
                f"companion {companion.companion_id!r} has no default memory realm"
            )
        if not genome_id:
            raise ResolvePrecondition(
                f"companion {companion.companion_id!r} has no current genome"
            )
        realm = await self._data_store.memory_repo.get_realm(memory_realm_id)
        if realm is None:
            raise ResolveNotFound(f"memory realm {memory_realm_id!r} not found")
        if realm.status != "active":
            raise ResolvePrecondition(f"memory realm {memory_realm_id!r} is {realm.status}")
        genome = await self._data_store.persona_repo.get_genome(genome_id)
        if genome is None:
            raise ResolveNotFound(f"genome {genome_id!r} not found")
        if genome.companion_id != companion.companion_id:
            raise ResolvePrecondition(
                f"genome {genome_id!r} belongs outside companion {companion.companion_id!r}"
            )
        if genome.status != "committed":
            raise ResolvePrecondition(f"genome {genome_id!r} is {genome.status}")
        return ResolvedContext(
            owner_id=companion.owner_id,
            companion_id=companion.companion_id,
            device_id=None,
            memory_realm_id=memory_realm_id,
            genome_id=genome_id,
        )
