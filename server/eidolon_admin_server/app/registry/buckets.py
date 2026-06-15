"""NATS KV bucket specs for admin-owned data.

What goes here vs not:
    - Tenants: admin-only concept, no sub-project equivalent → stays here.
    - Device↔Agent bindings: a cross-project editorial decision the operator
      makes in admin UI, so the binding fact-of-record lives here. The
      *device* itself lives in hub; the *agent* itself lives in agent project.
    - NOT here: templates (agent project owns), users (memory project owns),
      agents (agent project owns), device fact (hub project owns). Admin
      reads those by calling the respective sub-project's REST API.
    - User metadata also does not live here anymore; admin persists it in
      local SQLite so user CRUD is not coupled to NATS KV.

Bucket naming:
    All admin-owned buckets are prefixed ``eidolon_admin_`` so a NATS-side
    inspector can tell at a glance which project owns each stream. This
    matches the pre-existing convention from devices/repository.py.

Size limits:
    Each value is a small JSON object (tenant metadata, binding pointer).
    4 KB is generous — current entities are <500 B. The cap exists to fail
    fast if someone accidentally tries to dump a large blob into the
    registry by mistake.

History:
    Matches existing buckets (10 revisions). Free per-key undo trail
    available later if we want to surface "who-changed-what" in UI.
"""
from __future__ import annotations

from ..nats_kv import BucketSpec

# 4 KB caps tenant + binding JSON. Both shapes are flat (a tenant has
# a name + timestamp; a binding has an agent_id + timestamp). If we ever
# need to store more here, prefer adding a new bucket over inflating these.
MAX_TENANT_SIZE_BYTES = 4 * 1024
MAX_BINDING_SIZE_BYTES = 4 * 1024

# 10 revisions per key — same trade-off as devices/repository.py.
HISTORY_DEPTH = 10


TENANTS_BUCKET = BucketSpec(
    name="eidolon_admin_tenants",
    max_value_size=MAX_TENANT_SIZE_BYTES,
    history=HISTORY_DEPTH,
)


DEVICE_BINDINGS_BUCKET = BucketSpec(
    name="eidolon_admin_device_bindings",
    max_value_size=MAX_BINDING_SIZE_BYTES,
    history=HISTORY_DEPTH,
)


# Per-agent admin-side metadata (Phase 29.F). Agent project owns the
# persona instance (SQL row); admin's KV layers on a single-key handle
# so the wire surface is ``/api/agents/{agent_id}`` rather than
# ``/api/agents/{tenant}/{user}/{instance}`` (agent's internal composite
# key). Stored fields:
#   - tenant_id, user_id, template_id, template_revision: routing info
#     so admin can resolve agent_id → agent project's composite PK
#     without scanning persona_instances on every request
#   - display_name: operator-chosen label
#   - created_at: stamped by admin
AGENTS_METADATA_BUCKET = BucketSpec(
    name="eidolon_admin_agents_metadata",
    max_value_size=MAX_BINDING_SIZE_BYTES,
    history=HISTORY_DEPTH,
)


# Iterable for the lifespan hook to ``ensure_bucket`` all of these at
# admin startup. Order doesn't matter (ensure is idempotent + independent).
ALL_BUCKETS: tuple[BucketSpec, ...] = (
    TENANTS_BUCKET,
    DEVICE_BINDINGS_BUCKET,
    AGENTS_METADATA_BUCKET,
)
