"""Tenant business rules.

Three rules enforced here (not in router, not in repository):

  1. **tenant_id is the primary key** — immutable after creation. Renames
     go through ``display_name``. PUT only accepts ``display_name``.

  2. **Cannot delete the last tenant.** A live admin must always have at
     least one tenant to scope users/agents/devices against. Operators
     who want to wipe state should delete users/agents/devices first,
     then leave the default tenant alone.

  3. **No cross-entity cascade in this phase.** Tenant has no users or
     agents pointing at it yet (those entity modules are 29.E/F). Once
     they exist, the cascade hook here will: when a tenant is deleted,
     iterate over its users → delete each (which cascades into agents,
     unbinds devices). Until then, deleting a tenant just removes the
     tenant record; orphan referrers (if any) are a no-op.

The seed helper ``seed_default()`` is idempotent — admin calls it during
lifespan startup to guarantee the default tenant exists before any
other entity tries to reference it.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

from ..schemas.tenant import (
    CreateTenantRequest,
    TenantSpec,
    UpdateTenantRequest,
)
from .repository import TenantRepository

logger = logging.getLogger(__name__)


# Callable signature: given a tenant_id, return how many users still
# belong to this tenant. The orchestrator uses this for the cascade
# refuse-on-delete check. Injected by main.py's lifespan after both
# tenant and user orchestrators exist.
UserRefcountProvider = Callable[[str], Awaitable[int]]


# ---- exceptions (router maps to HTTP status codes) -------------------------


class TenantError(Exception):
    """Base — admin's exception handler maps these via ``status_code``."""

    status_code: int = 500


class TenantAlreadyExists(TenantError):
    status_code = 409


class TenantNotFound(TenantError):
    status_code = 404


class LastTenantError(TenantError):
    """Refused: would leave admin with zero tenants."""

    status_code = 409


class TenantInUse(TenantError):
    """Refused: at least one user still belongs to this tenant. Phase
    29.E.1 added this gate — closes the cascade gap noted in 29.A doc."""

    status_code = 409


# ---- orchestration ---------------------------------------------------------


# The tenant id used by ``seed_default``. Other modules import this constant
# (rather than the string literal) so a rename happens in one place.
DEFAULT_TENANT_ID = "default"
DEFAULT_TENANT_DISPLAY_NAME = "Default"


class TenantOrchestrator:
    """Tenant CRUD + business invariants. Stateless apart from the repo.

    The Users module (29.E) plugs in a refcount provider via
    :meth:`set_user_refcount_provider` so this orchestrator can refuse
    to delete a tenant that still has users. Done via setter rather
    than constructor to avoid a circular dependency at module-build
    time (Users needs Tenants for create-user's tenant validation;
    Tenants needs Users for delete-tenant's cascade check).
    """

    def __init__(self, repo: TenantRepository) -> None:
        self._repo = repo
        self._user_refcount_provider: Optional[UserRefcountProvider] = None

    def set_user_refcount_provider(
        self, provider: Optional[UserRefcountProvider]
    ) -> None:
        """Wire / unwire the cascade hook. Pass ``None`` to detach
        (useful in tests that want to verify the no-cascade behavior)."""
        self._user_refcount_provider = provider

    async def list_all(self) -> list[TenantSpec]:
        tenants = await self._repo.list_all()
        # Stable order for UI: sort by created_at ASC so the default
        # (seeded first) is always first; ties broken by id.
        return sorted(tenants, key=lambda t: (t.created_at, t.tenant_id))

    async def get(self, tenant_id: str) -> TenantSpec:
        spec = await self._repo.get(tenant_id)
        if spec is None:
            raise TenantNotFound(f"tenant {tenant_id!r} not found")
        return spec

    async def create(self, body: CreateTenantRequest) -> TenantSpec:
        """Create a tenant. ``tenant_id`` must be unique.

        Returns the freshly built spec (with server-stamped ``created_at``).
        """
        if await self._repo.get(body.tenant_id) is not None:
            raise TenantAlreadyExists(
                f"tenant {body.tenant_id!r} already exists; pick a different id"
            )
        spec = TenantSpec(
            tenant_id=body.tenant_id,
            display_name=body.display_name,
            created_at=datetime.now(timezone.utc),
        )
        await self._repo.put(spec)
        logger.info("tenant_created tenant_id=%s", body.tenant_id)
        return spec

    async def update(self, tenant_id: str, body: UpdateTenantRequest) -> TenantSpec:
        """Update ``display_name`` only. ``tenant_id`` is immutable (it's the PK).

        We re-read, mutate, write — atomicity-wise NATS KV's history makes
        this safe even under concurrent writes: each PUT bumps the
        revision; a lost-update race surfaces in the history.
        """
        spec = await self.get(tenant_id)
        updated = spec.model_copy(update={"display_name": body.display_name})
        await self._repo.put(updated)
        logger.info("tenant_updated tenant_id=%s", tenant_id)
        return updated

    async def delete(self, tenant_id: str) -> None:
        """Delete a tenant. Three guards in order of specificity:

          1. **404** if the tenant doesn't exist (most specific —
             operator typo or stale UI).
          2. **TenantInUse** if any user still references this tenant
             (cascade refuse — protects against orphaning users into
             a deleted tenant; operator must delete those users first).
          3. **LastTenantError** if this is the only tenant
             (admin must always have ≥1 tenant for scoping).

        Ordering chosen so the operator gets the most actionable error:
        "you mistyped the id" > "you have users to migrate first" >
        "you can't go to zero tenants".
        """
        spec = await self._repo.get(tenant_id)
        if spec is None:
            raise TenantNotFound(f"tenant {tenant_id!r} not found")

        # Cascade refcount check. Only enabled when a refcount provider
        # is wired (Users module installs it at lifespan). Without one,
        # we skip — useful for tests that exercise just the tenant logic.
        if self._user_refcount_provider is not None:
            refcount = await self._user_refcount_provider(tenant_id)
            if refcount > 0:
                raise TenantInUse(
                    f"cannot delete tenant {tenant_id!r}: {refcount} user(s) "
                    "still belong to it. Delete (or migrate) those users "
                    "first, then retry."
                )

        if await self._repo.count() <= 1:
            raise LastTenantError(
                f"cannot delete tenant {tenant_id!r}: it is the only tenant. "
                "Create another tenant first, then retry the delete."
            )
        await self._repo.delete(tenant_id)
        logger.info("tenant_deleted tenant_id=%s", tenant_id)


# ---- bootstrap helper ------------------------------------------------------


async def seed_default(orchestrator: TenantOrchestrator) -> bool:
    """Ensure the default tenant exists. Returns True if it was created
    by this call, False if it was already there. Idempotent.

    Called from admin's lifespan startup so by the time any other module
    issues a ``GET /api/tenants`` the default row is present.
    """
    try:
        await orchestrator.get(DEFAULT_TENANT_ID)
        return False
    except TenantNotFound:
        pass
    try:
        await orchestrator.create(
            CreateTenantRequest(
                tenant_id=DEFAULT_TENANT_ID,
                display_name=DEFAULT_TENANT_DISPLAY_NAME,
            )
        )
    except TenantAlreadyExists:
        # Raced with another startup; both saw missing, both tried to
        # create. The repo's put is last-write-wins so this is harmless;
        # treat as already-existed.
        return False
    logger.info("tenant_seed_default created tenant_id=%s", DEFAULT_TENANT_ID)
    return True
