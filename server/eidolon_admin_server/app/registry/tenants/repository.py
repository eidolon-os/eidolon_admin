"""NATS KV persistence for tenants.

Thin adapter between the orchestrator (which speaks Pydantic ``TenantSpec``)
and the KV client (which speaks bytes). Only this layer knows the bucket
name and key naming; everything above is implementation-agnostic about
where tenants live.

If we ever move tenants to a different backing store (e.g. SQLite), only
this file changes.
"""
from __future__ import annotations

import logging
from typing import Iterable

from ...nats_kv import KVClient, from_json_bytes, to_json_bytes
from ..buckets import TENANTS_BUCKET
from ..keys import tenant_key
from ..schemas.tenant import TenantSpec

logger = logging.getLogger(__name__)


class TenantRepository:
    """KV-backed store for tenants.

    Construction is cheap (just captures the client); ``ensure_bucket`` is
    called once at lifespan startup by admin's main() — not here — so this
    class never blocks on broker handshakes at import time.
    """

    def __init__(self, kv: KVClient) -> None:
        self._kv = kv

    async def get(self, tenant_id: str) -> TenantSpec | None:
        """Return the tenant, or ``None`` if no key exists."""
        raw = await self._kv.get(TENANTS_BUCKET.name, tenant_key(tenant_id))
        if raw is None:
            return None
        try:
            return TenantSpec.model_validate(from_json_bytes(raw))
        except Exception:
            # A bucket entry that doesn't parse means someone (or an
            # older admin version) wrote a stale shape. Log loudly and
            # treat as absent — re-creating fixes it cleanly.
            logger.exception("tenants: malformed KV entry %s", tenant_id)
            return None

    async def put(self, spec: TenantSpec) -> None:
        """Persist (create or overwrite). Caller has already enforced
        uniqueness / mutability rules; this is a flat write."""
        await self._kv.put(
            TENANTS_BUCKET.name,
            tenant_key(spec.tenant_id),
            to_json_bytes(spec.model_dump(mode="json")),
        )

    async def delete(self, tenant_id: str) -> None:
        """Remove the key. Idempotent — deleting a non-existent key is fine."""
        await self._kv.delete(TENANTS_BUCKET.name, tenant_key(tenant_id))

    async def list_all(self) -> list[TenantSpec]:
        """Return every tenant. Order is bucket-natural (no sort applied);
        the router/orchestrator sorts by display_name if it cares."""
        keys = await self._kv.list_keys(TENANTS_BUCKET.name, prefix="tenant.")
        out: list[TenantSpec] = []
        for key in keys:
            raw = await self._kv.get(TENANTS_BUCKET.name, key)
            if raw is None:
                continue
            try:
                out.append(TenantSpec.model_validate(from_json_bytes(raw)))
            except Exception:
                logger.exception("tenants: malformed KV entry at key %s", key)
        return out

    async def count(self) -> int:
        """Number of tenants. Used by the "can't delete last tenant" guard
        and by the seed-default-on-empty bootstrap helper."""
        keys = await self._kv.list_keys(TENANTS_BUCKET.name, prefix="tenant.")
        return len(keys)
