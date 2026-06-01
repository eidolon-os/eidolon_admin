"""Tenants — top-level resource scope, owned end-to-end by admin.

This is the simplest of the five Phase 29 entities because there is no
sub-project to call: Tenant is purely an admin concept. The module
follows the same 4-layer split as ``devices/`` (router / orchestrator
/ repository / schemas).

Schemas live one level up at ``..schemas.tenant`` so they can be
re-exported alongside the other entity schemas; everything else
(persistence + business rules + HTTP) is in this module.

Default tenant ``default`` is seeded by ``orchestrator.seed_default``
on first admin startup. Single-tenant deployments never have to think
about tenants; the UI hides the selector when only one exists.
"""
from .orchestrator import (
    LastTenantError,
    TenantAlreadyExists,
    TenantInUse,
    TenantNotFound,
    TenantOrchestrator,
    seed_default,
)
from .repository import TenantRepository
from .router import router

__all__ = [
    "LastTenantError",
    "TenantAlreadyExists",
    "TenantInUse",
    "TenantNotFound",
    "TenantOrchestrator",
    "TenantRepository",
    "router",
    "seed_default",
]
