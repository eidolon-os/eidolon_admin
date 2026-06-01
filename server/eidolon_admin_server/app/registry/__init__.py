"""Five-entity resource registry.

This module is the admin's bookkeeping layer for the Phase 29 resource model:

    Tenant  →  Template  →  User  →  Agent  →  Device
        (admin)   (agent)    (memory)  (agent)   (hub + admin)

Admin DOES NOT own the business implementations:
    - Template authoring + rendering lives in the eidolon_agent project.
    - User palaces + memory recall live in the eidolon_memory project.
    - Device discovery + approval state lives in the eidolon_hub project.

Admin DOES own:
    - The cross-cutting bindings (which agent belongs to which user, which
      device is bound to which agent).
    - The Tenant concept (no sub-project has it).
    - The composed REST surface that lets web/UI/channel speak to one
      authoritative source instead of polling three sub-projects.

Submodules:
    schemas/   Pydantic wire shapes for each entity (admin's REST contract).
    buckets    NATS KV bucket specs admin owns (tenants + device bindings).
    keys       Key naming conventions for admin's KV (kept private).

The actual routers / orchestrators / repositories per entity arrive in
later phases (29.C through 29.G). This phase only locks the schemas and
bucket layout — nothing wired into FastAPI yet.

See docs/architecture/phase-29-five-entity-model.md for the full design.
"""
from .buckets import (
    ALL_BUCKETS,
    DEVICE_BINDINGS_BUCKET,
    HISTORY_DEPTH,
    MAX_BINDING_SIZE_BYTES,
    MAX_TENANT_SIZE_BYTES,
    TENANTS_BUCKET,
    USERS_METADATA_BUCKET,
)
from .keys import device_binding_key, tenant_key, user_metadata_key

__all__ = [
    "ALL_BUCKETS",
    "DEVICE_BINDINGS_BUCKET",
    "HISTORY_DEPTH",
    "MAX_BINDING_SIZE_BYTES",
    "MAX_TENANT_SIZE_BYTES",
    "TENANTS_BUCKET",
    "USERS_METADATA_BUCKET",
    "device_binding_key",
    "tenant_key",
    "user_metadata_key",
]
