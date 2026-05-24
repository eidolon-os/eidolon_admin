"""NATS KV business-shape adapter for device bindings.

This is the *only* module in the codebase that knows the key naming
convention (``device.<id>`` in mappings; ``agent.<id>`` in souls / agents)
and the JSON shape stored in each bucket. The orchestrator above uses
business-named methods (``get_mapping``, ``add_agent``, …) and never sees
a NATS key string.

Three buckets, three responsibilities:

- ``mappings``: device → list of agents + active pointer. Small (one row
  per device, a few hundred bytes).
- ``souls``: agent_id → markdown text. Large per row (1-200 KB).
- ``agents``: agent_id → metadata (template id, owner, timestamps). Small.

Bucket isolation is intentional: souls grow large and may need different
retention policies than mappings; separating them keeps that knob
independent.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..nats_kv import BucketSpec, KVClient, from_json_bytes, to_json_bytes

logger = logging.getLogger(__name__)


# ---- bucket configuration (single source of truth) -------------------------

# 256 KB caps a soul.md; render output is ~1-50 KB so this leaves ~5x headroom
# for operator hand-edits and additional sections.
MAX_SOUL_SIZE_BYTES = 256 * 1024
# 4 KB caps mappings / agents JSON; even with 50 agents per device the
# mapping is far under this.
MAX_META_SIZE_BYTES = 4 * 1024

# History=10 lets NATS KV retain the last 10 revisions of each key — gives
# us a free per-key undo trail. Not exposed in UI this phase but earns us
# the option later.
HISTORY_DEPTH = 10

MAPPINGS_BUCKET = BucketSpec(
    name="eidolon_admin_mappings",
    max_value_size=MAX_META_SIZE_BYTES,
    history=HISTORY_DEPTH,
)
SOULS_BUCKET = BucketSpec(
    name="eidolon_admin_souls",
    max_value_size=MAX_SOUL_SIZE_BYTES,
    history=HISTORY_DEPTH,
)
AGENTS_BUCKET = BucketSpec(
    name="eidolon_admin_agents",
    max_value_size=MAX_META_SIZE_BYTES,
    history=HISTORY_DEPTH,
)


ALL_BUCKETS: tuple[BucketSpec, ...] = (MAPPINGS_BUCKET, SOULS_BUCKET, AGENTS_BUCKET)


# ---- key naming (kept private to this module) ------------------------------


def _mapping_key(device_id: str) -> str:
    return f"device.{device_id}"


def _agent_key(agent_id: str) -> str:
    return f"agent.{agent_id}"


# NATS KV keys allow letters, digits, ``.``, ``_``, ``-``, ``/`` and ``=``.
# Device IDs and agent IDs are validated by callers before reaching here;
# this helper just exposes the valid character set for orchestrator-level
# checks if needed.
_NATS_KV_VALID_KEY_CHARS = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-/=:"
)


def is_valid_id(value: str) -> bool:
    """True if ``value`` is non-empty and uses only NATS-KV-safe characters.

    Used by orchestrator before composing a key — we never want to pass
    something like ``"foo bar"`` to NATS and discover server-side rejection
    halfway through a write.
    """
    return bool(value) and all(c in _NATS_KV_VALID_KEY_CHARS for c in value)


# ---- business-shape data classes -------------------------------------------


@dataclass
class Mapping:
    """In-memory representation of one ``mappings`` value.

    Mutable on purpose: orchestrator picks one up, mutates, hands it back to
    ``put_mapping``. Keeps the call sites readable (`m.agent_ids.append(...)`
    is clearer than re-constructing).
    """

    user_id: str
    agent_ids: list[str] = field(default_factory=list)
    active_agent_id: str | None = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class AgentMeta:
    template_id: str
    template_revision: int
    owner_user_id: str
    owner_device_id: str
    created_at: datetime
    updated_at: datetime


# ---- repository -----------------------------------------------------------


class DeviceBindingRepository:
    """All NATS reads / writes for the device-binding feature live here.

    The orchestrator calls methods like ``add_agent(device_id, agent_id,
    user_id, ...)``; it never composes ``device.<id>`` strings or JSON
    payloads directly. That's what lets us change key naming or payload
    shape without touching anything above.
    """

    def __init__(self, kv: KVClient) -> None:
        self._kv = kv

    # ---- bucket admin -------------------------------------------------

    async def ensure_buckets(self) -> None:
        for spec in ALL_BUCKETS:
            await self._kv.ensure_bucket(spec)

    # ---- mappings -----------------------------------------------------

    async def get_mapping(self, device_id: str) -> Mapping | None:
        raw = await self._kv.get(MAPPINGS_BUCKET.name, _mapping_key(device_id))
        if raw is None:
            return None
        return _mapping_from_payload(from_json_bytes(raw))

    async def put_mapping(self, device_id: str, mapping: Mapping) -> None:
        mapping.updated_at = datetime.now(timezone.utc)
        payload = _mapping_to_payload(mapping)
        await self._kv.put(MAPPINGS_BUCKET.name, _mapping_key(device_id), to_json_bytes(payload))

    async def delete_mapping(self, device_id: str) -> None:
        await self._kv.delete(MAPPINGS_BUCKET.name, _mapping_key(device_id))

    async def list_mapped_devices(self) -> list[str]:
        """Return all device_ids that have a mapping row.

        Used by ``list_devices`` to surface NATS-known devices that hub
        might not have a record of (edge case: a device's hub record got
        deleted but its binding remains — admin still shows the orphan so
        operator can clean up).
        """
        keys = await self._kv.list_keys(MAPPINGS_BUCKET.name, prefix="device.")
        return [k.removeprefix("device.") for k in keys]

    # ---- agents (metadata bucket) ------------------------------------

    async def get_agent_meta(self, agent_id: str) -> AgentMeta | None:
        raw = await self._kv.get(AGENTS_BUCKET.name, _agent_key(agent_id))
        if raw is None:
            return None
        return _agent_meta_from_payload(from_json_bytes(raw))

    async def put_agent_meta(self, agent_id: str, meta: AgentMeta) -> None:
        meta.updated_at = datetime.now(timezone.utc)
        await self._kv.put(
            AGENTS_BUCKET.name,
            _agent_key(agent_id),
            to_json_bytes(_agent_meta_to_payload(meta)),
        )

    async def delete_agent_meta(self, agent_id: str) -> None:
        await self._kv.delete(AGENTS_BUCKET.name, _agent_key(agent_id))

    async def agent_exists(self, agent_id: str) -> bool:
        """Cheap existence check used for UUID-collision retries in orchestrator."""
        raw = await self._kv.get(AGENTS_BUCKET.name, _agent_key(agent_id))
        return raw is not None

    # ---- souls (markdown bucket) -------------------------------------

    async def get_soul(self, agent_id: str) -> str | None:
        raw = await self._kv.get(SOULS_BUCKET.name, _agent_key(agent_id))
        if raw is None:
            return None
        return raw.decode("utf-8")

    async def put_soul(self, agent_id: str, markdown: str) -> int:
        """Write soul text. Returns the byte length actually written.

        Caller is expected to have already checked length ≤
        ``MAX_SOUL_SIZE_BYTES`` (so they can return a clean 413 rather than
        letting NATS reject mid-write).
        """
        encoded = markdown.encode("utf-8")
        await self._kv.put(SOULS_BUCKET.name, _agent_key(agent_id), encoded)
        return len(encoded)

    async def delete_soul(self, agent_id: str) -> None:
        await self._kv.delete(SOULS_BUCKET.name, _agent_key(agent_id))


# ---- payload shape (kept private to this module) ---------------------------
#
# The functions below are the only place that knows the on-the-wire JSON
# shape. Tests can target them directly to lock the schema; everything else
# works in terms of the dataclasses above.


def _mapping_to_payload(m: Mapping) -> dict[str, Any]:
    return {
        "user_id": m.user_id,
        "agent_ids": list(m.agent_ids),
        "active_agent_id": m.active_agent_id,
        "updated_at": m.updated_at.astimezone(timezone.utc).isoformat(),
    }


def _mapping_from_payload(payload: dict[str, Any]) -> Mapping:
    return Mapping(
        user_id=payload["user_id"],
        agent_ids=list(payload.get("agent_ids", [])),
        active_agent_id=payload.get("active_agent_id"),
        updated_at=_parse_dt(payload.get("updated_at")),
    )


def _agent_meta_to_payload(m: AgentMeta) -> dict[str, Any]:
    return {
        "template_id": m.template_id,
        "template_revision": m.template_revision,
        "owner_user_id": m.owner_user_id,
        "owner_device_id": m.owner_device_id,
        "created_at": m.created_at.astimezone(timezone.utc).isoformat(),
        "updated_at": m.updated_at.astimezone(timezone.utc).isoformat(),
    }


def _agent_meta_from_payload(payload: dict[str, Any]) -> AgentMeta:
    return AgentMeta(
        template_id=payload["template_id"],
        template_revision=int(payload["template_revision"]),
        owner_user_id=payload["owner_user_id"],
        owner_device_id=payload["owner_device_id"],
        created_at=_parse_dt(payload["created_at"]),
        updated_at=_parse_dt(payload["updated_at"]),
    )


def _parse_dt(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
