"""Tenant — top-level resource scope.

Tenant is the **only** Phase 29 entity that admin owns end-to-end. No
sub-project has a tenant concept; admin invents it as a grouping for
users/agents/devices in case the deployment ever serves multiple
isolated parties.

Default deployment ships with a single tenant ``"default"``. UI hides
the tenant selector until more than one exists (single-tenant mode is
the common case and shouldn't pay for multi-tenant UI complexity).

Persistence:
    Admin's local registry SQLite database.

Cascade rule (enforced by orchestrator):
    Deleting a tenant deletes its users (which in turn cascade to
    that user's agents and unbind those agents from any devices).
    Cannot delete the last tenant.
"""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from eidolon_sdk.registry.ids import validate_registry_id
from eidolon_sdk.registry.models import TenantSpec


class CreateTenantRequest(BaseModel):
    tenant_id: str = Field(..., min_length=1, max_length=64)
    display_name: str = Field(..., min_length=1, max_length=128)

    @field_validator("tenant_id")
    @classmethod
    def _check_id(cls, v: str) -> str:
        return validate_registry_id(v, field_name="tenant_id")


class UpdateTenantRequest(BaseModel):
    """Only display_name is mutable. tenant_id is immutable (it's the PK)."""

    display_name: str = Field(..., min_length=1, max_length=128)


class TenantListResponse(BaseModel):
    tenants: list[TenantSpec]
