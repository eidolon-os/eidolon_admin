"""NATS KV key-naming for admin-owned buckets.

Centralized so the rest of the code never builds keys with string concat
inline. If we ever need to rename a key prefix (or add a namespace),
this is the only file to touch.

Per the NATS KV spec, keys are dot-separated tokens. We use a single-level
prefix (``tenant.`` / ``device.``) so list_keys(prefix=...) works cleanly.
"""
from __future__ import annotations


def tenant_key(tenant_id: str) -> str:
    """Key under TENANTS_BUCKET for a single tenant record."""
    return f"tenant.{tenant_id}"


def device_binding_key(device_id: str) -> str:
    """Key under DEVICE_BINDINGS_BUCKET for one device→agent binding."""
    return f"device.{device_id}"


def user_metadata_key(user_id: str) -> str:
    """Key under USERS_METADATA_BUCKET for one user's admin-side metadata."""
    return f"user.{user_id}"


def agent_metadata_key(agent_id: str) -> str:
    """Key under AGENTS_METADATA_BUCKET for one agent's admin-side metadata."""
    return f"agent.{agent_id}"
