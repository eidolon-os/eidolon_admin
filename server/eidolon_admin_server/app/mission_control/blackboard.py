"""Read-only access to the shared Runtime Device Blackboard.

Hub remains the only writer. Mission Control opens the existing NATS KV bucket
and returns each current value verbatim as decoded JSON; it never creates a
bucket, watches it into a cache, or projects a second source of truth.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, Request

from eidolon_sdk.biz.body import DEVICE_BLACKBOARD_BUCKET, owner_device_blackboard_key

from .schemas import RuntimeBlackboardEntry, RuntimeBlackboardResponse


async def read_runtime_blackboard(
    request: Request,
    *,
    owner_id: str | None = None,
) -> RuntimeBlackboardResponse:
    client = getattr(request.app.state, "nats_kv", None)
    if client is None:
        raise HTTPException(status_code=503, detail="Runtime Blackboard KV client unavailable")

    try:
        # open_bucket is intentionally non-creating: a GET must never establish
        # or mutate the Hub-owned Blackboard bucket.
        await client.open_bucket(DEVICE_BLACKBOARD_BUCKET)
        if owner_id:
            keys = [owner_device_blackboard_key(owner_id)]
        else:
            keys = sorted(
                key
                for key in await client.list_keys(DEVICE_BLACKBOARD_BUCKET, prefix="owner.")
                if key.endswith(".current")
            )
        entries = await _read_entries(client, keys, expected_owner_id=owner_id)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - translate infrastructure failures at the boundary
        raise HTTPException(
            status_code=503,
            detail=f"Runtime Blackboard unavailable: {exc}",
        ) from exc

    return RuntimeBlackboardResponse(
        generated_at=datetime.now(UTC),
        bucket=DEVICE_BLACKBOARD_BUCKET,
        owner_filter=owner_id,
        entries=entries,
    )


async def _read_entries(
    client: Any,
    keys: list[str],
    *,
    expected_owner_id: str | None,
) -> list[RuntimeBlackboardEntry]:
    entries: list[RuntimeBlackboardEntry] = []
    for key in keys:
        raw = await client.get(DEVICE_BLACKBOARD_BUCKET, key)
        if raw is None:
            continue
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            entries.append(RuntimeBlackboardEntry(key=key, error=f"invalid JSON: {exc}"))
            continue
        if not isinstance(value, dict):
            entries.append(RuntimeBlackboardEntry(key=key, error="snapshot is not a JSON object"))
            continue

        actual_owner_id = value.get("owner_id")
        actual_owner = actual_owner_id if isinstance(actual_owner_id, str) else None
        if expected_owner_id is not None and actual_owner != expected_owner_id:
            # The selected owner maps to one opaque current key. A mismatch is
            # corruption and must not be presented as if it belonged in scope.
            entries.append(
                RuntimeBlackboardEntry(
                    key=key,
                    owner_id=actual_owner,
                    error="snapshot owner does not match the owner-scoped key",
                )
            )
            continue
        key_error = ""
        if actual_owner is None:
            key_error = "snapshot owner_id is missing or invalid"
        elif owner_device_blackboard_key(actual_owner) != key:
            # In the admin-wide view preserve the raw value for diagnosis, but
            # surface the broken physical owner isolation rather than silently
            # re-attributing the snapshot from its payload.
            key_error = "snapshot owner does not match the owner-scoped key"
        entries.append(
            RuntimeBlackboardEntry(
                key=key,
                owner_id=actual_owner,
                snapshot=value,
                error=key_error,
            )
        )
    return entries


__all__ = ["read_runtime_blackboard"]
