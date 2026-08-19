"""Pure NATS JetStream KV primitives.

This package exposes only infrastructure operations (connect, ensure_bucket,
get/put/delete/list_keys, watch-stub). It deliberately does NOT know about
mappings, souls, or any other business concept — that lives in the
``devices`` module's repository layer one level up.
"""
from .client import (
    BucketSpec,
    KVClient,
    default_nats_url,
    from_json_bytes,
    to_json_bytes,
)

__all__ = [
    "BucketSpec",
    "KVClient",
    "default_nats_url",
    "from_json_bytes",
    "to_json_bytes",
]
