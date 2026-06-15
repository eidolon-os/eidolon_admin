"""NATS KV key-naming for admin-owned buckets.

Centralized so the rest of the code never builds keys with string concat
inline. If we ever need to rename a key prefix (or add a namespace),
this is the only file to touch.

Per the NATS KV spec, keys are dot-separated tokens. We use a single-level
prefix (``tenant.`` / ``device.``) so list_keys(prefix=...) works cleanly.
"""
from __future__ import annotations

import base64
import re

_NATS_KV_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*$")


def _key_token(value: str) -> str:
    raw = value.encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_key_token(value: str) -> str:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii")).decode("utf-8")


def tenant_key(tenant_id: str) -> str:
    """Key under TENANTS_BUCKET for a single tenant record."""
    return f"tenant.{tenant_id}"


def device_binding_key(device_id: str) -> str:
    """Key under DEVICE_BINDINGS_BUCKET for one device→agent binding."""
    return f"device.v1.{_key_token(device_id)}"


def legacy_device_binding_key(device_id: str) -> str | None:
    """Previous device binding key shape, kept for migration reads/deletes."""
    key = f"device.{device_id}"
    return key if _NATS_KV_KEY_RE.fullmatch(key) else None


def decode_device_binding_key(key: str) -> str | None:
    if key.startswith("device.v1."):
        try:
            return _decode_key_token(key.removeprefix("device.v1."))
        except Exception:
            return None
    if key.startswith("device."):
        return key.removeprefix("device.")
    return None


def agent_metadata_key(agent_id: str) -> str:
    """Key under AGENTS_METADATA_BUCKET for one agent's admin-side metadata."""
    return f"agent.{agent_id}"
