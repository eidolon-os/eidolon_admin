"""Tenant — top-level resource scope.

Tenant is the **only** Phase 29 entity that admin owns end-to-end. No
sub-project has a tenant concept; admin invents it as a grouping for
users/agents/devices in case the deployment ever serves multiple
isolated parties.

Default deployment ships with a single tenant ``"default"``. UI hides
the tenant selector until more than one exists (single-tenant mode is
the common case and shouldn't pay for multi-tenant UI complexity).

Persistence:
    NATS KV bucket ``eidolon_admin_tenants``, key ``tenant.<id>``.

Cascade rule (enforced by orchestrator):
    Deleting a tenant deletes its users (which in turn cascade to
    that user's agents and unbind those agents from any devices).
    Cannot delete the last tenant.
"""
from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

# Same character class admin already uses for tenant_id / user_id / template_id:
# letters, digits, underscore, hyphen — NATS KV key charset minus dot.
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _validate_id(value: str, *, field_name: str) -> str:
    if not _ID_RE.match(value):
        raise ValueError(
            f"{field_name} must be 1-64 chars of [A-Za-z0-9_-] "
            f"(NATS KV key charset); got {value!r}"
        )
    return value


class TenantSpec(BaseModel):
    """The canonical persisted shape of a tenant.

    Written to NATS KV as JSON; round-trips losslessly. ``created_at``
    is stamped by the orchestrator on creation and never updated
    afterwards (renames go through display_name, never through id).
    """

    tenant_id: str = Field(..., min_length=1, max_length=64)
    display_name: str = Field(..., min_length=1, max_length=128)
    created_at: datetime

    @field_validator("tenant_id")
    @classmethod
    def _check_id(cls, v: str) -> str:
        return _validate_id(v, field_name="tenant_id")


class CreateTenantRequest(BaseModel):
    tenant_id: str = Field(..., min_length=1, max_length=64)
    display_name: str = Field(..., min_length=1, max_length=128)

    @field_validator("tenant_id")
    @classmethod
    def _check_id(cls, v: str) -> str:
        return _validate_id(v, field_name="tenant_id")


class UpdateTenantRequest(BaseModel):
    """Only display_name is mutable. tenant_id is immutable (it's the PK)."""

    display_name: str = Field(..., min_length=1, max_length=128)


class TenantListResponse(BaseModel):
    tenants: list[TenantSpec]
