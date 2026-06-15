"""Tests for the Tenants module (Phase 29.C).

Per-test SQLite database so parallel runs don't collide. We test:

  - repository: SQLite round-trip, list/count, missing-key returns None
  - orchestrator: create / get / update / delete + the three business
    rules (immutable id, can't delete last, 404 vs 409 ordering)
  - seed_default: idempotent, races safely
  - router (TestClient): each endpoint's status code + envelope shape

Each test name reads as the contract it pins down.
"""
from __future__ import annotations

from typing import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI

from eidolon_admin_server.app.registry.schemas.tenant import (
    CreateTenantRequest,
    TenantSpec,
    UpdateTenantRequest,
)
from eidolon_admin_server.app.registry.tenants import (
    LastTenantError,
    TenantAlreadyExists,
    TenantInUse,
    TenantNotFound,
    TenantOrchestrator,
    TenantRepository,
    router as tenants_router,
    seed_default,
)


# ---- fixtures ---------------------------------------------------------------


@pytest.fixture
async def repo(tmp_path) -> TenantRepository:
    """Repo backed by a per-test registry DB so parallel runs / re-runs
    never see leaked state from prior tests."""
    return TenantRepository(tmp_path / "registry.sqlite3")


@pytest.fixture
async def orchestrator(repo: TenantRepository) -> TenantOrchestrator:
    return TenantOrchestrator(repo)


# ---- repository ------------------------------------------------------------


async def test_repository_get_returns_none_for_missing_key(repo: TenantRepository) -> None:
    assert await repo.get("ghost") is None


async def test_repository_put_then_get_round_trip(
    repo: TenantRepository,
) -> None:
    from datetime import datetime, timezone

    spec = TenantSpec(
        tenant_id="acme",
        display_name="Acme",
        created_at=datetime.now(timezone.utc),
    )
    await repo.put(spec)
    fetched = await repo.get("acme")
    assert fetched == spec


async def test_repository_count_and_list_all(repo: TenantRepository) -> None:
    from datetime import datetime, timedelta, timezone

    base = datetime.now(timezone.utc)
    await repo.put(TenantSpec(tenant_id="a", display_name="A", created_at=base))
    await repo.put(
        TenantSpec(
            tenant_id="b",
            display_name="B",
            created_at=base + timedelta(seconds=1),
        )
    )
    assert await repo.count() == 2
    listed = await repo.list_all()
    assert {t.tenant_id for t in listed} == {"a", "b"}


async def test_repository_delete_is_idempotent(repo: TenantRepository) -> None:
    """Two deletes of the same key must not raise — admin's cascade retry
    relies on this idempotency."""
    await repo.delete("ghost")
    await repo.delete("ghost")  # no exception


# ---- orchestrator ----------------------------------------------------------


async def test_orchestrator_create_stamps_created_at(
    orchestrator: TenantOrchestrator,
) -> None:
    spec = await orchestrator.create(
        CreateTenantRequest(tenant_id="acme", display_name="Acme")
    )
    assert spec.tenant_id == "acme"
    assert spec.display_name == "Acme"
    # created_at is set by the orchestrator (request body has no slot for it)
    assert spec.created_at is not None


async def test_orchestrator_create_rejects_duplicate(
    orchestrator: TenantOrchestrator,
) -> None:
    await orchestrator.create(
        CreateTenantRequest(tenant_id="acme", display_name="Acme")
    )
    with pytest.raises(TenantAlreadyExists):
        await orchestrator.create(
            CreateTenantRequest(tenant_id="acme", display_name="Acme Inc")
        )


async def test_orchestrator_get_missing_raises_404(
    orchestrator: TenantOrchestrator,
) -> None:
    with pytest.raises(TenantNotFound):
        await orchestrator.get("ghost")


async def test_orchestrator_update_changes_display_name_only(
    orchestrator: TenantOrchestrator,
) -> None:
    """``tenant_id`` is the PK — there is no API to rename it. ``display_name``
    is the only mutable field, and the request body enforces this."""
    original = await orchestrator.create(
        CreateTenantRequest(tenant_id="acme", display_name="Old")
    )
    updated = await orchestrator.update(
        "acme", UpdateTenantRequest(display_name="New")
    )
    assert updated.tenant_id == "acme"
    assert updated.display_name == "New"
    # created_at is preserved across update
    assert updated.created_at == original.created_at


async def test_orchestrator_update_missing_raises_404(
    orchestrator: TenantOrchestrator,
) -> None:
    with pytest.raises(TenantNotFound):
        await orchestrator.update("ghost", UpdateTenantRequest(display_name="X"))


async def test_orchestrator_delete_removes_record(
    orchestrator: TenantOrchestrator,
) -> None:
    """Two tenants → delete one → other remains. Verifies delete actually
    persists; not just a no-op."""
    await orchestrator.create(CreateTenantRequest(tenant_id="a", display_name="A"))
    await orchestrator.create(CreateTenantRequest(tenant_id="b", display_name="B"))
    await orchestrator.delete("a")
    with pytest.raises(TenantNotFound):
        await orchestrator.get("a")
    assert (await orchestrator.get("b")).tenant_id == "b"


async def test_orchestrator_delete_missing_raises_404(
    orchestrator: TenantOrchestrator,
) -> None:
    with pytest.raises(TenantNotFound):
        await orchestrator.delete("ghost")


# ---- cascade refcount (29.E.1) ----------------------------------------------


async def test_orchestrator_refuses_delete_when_users_reference_tenant(
    orchestrator: TenantOrchestrator,
) -> None:
    """Cascade gate: if a refcount provider says N>0 users belong to
    this tenant, delete refuses with TenantInUse (409). This protects
    against orphaning users into a deleted tenant."""
    await orchestrator.create(CreateTenantRequest(tenant_id="a", display_name="A"))
    await orchestrator.create(CreateTenantRequest(tenant_id="b", display_name="B"))

    # Wire a fake refcount provider that says 2 users reference 'a'.
    async def _refcount(tenant_id: str) -> int:
        return 2 if tenant_id == "a" else 0

    orchestrator.set_user_refcount_provider(_refcount)

    with pytest.raises(TenantInUse) as exc_info:
        await orchestrator.delete("a")
    assert "2 user(s)" in str(exc_info.value)
    # Tenant still present.
    assert (await orchestrator.get("a")).tenant_id == "a"

    # With refcount = 0, delete succeeds.
    await orchestrator.delete("b")  # b has refcount 0


async def test_orchestrator_skips_cascade_when_no_provider_set(
    orchestrator: TenantOrchestrator,
) -> None:
    """Backwards-compatible: without a refcount provider (tests, partial
    init), delete works as it did pre-29.E.1 — only last-tenant guard."""
    await orchestrator.create(CreateTenantRequest(tenant_id="a", display_name="A"))
    await orchestrator.create(CreateTenantRequest(tenant_id="b", display_name="B"))
    # No provider set — should proceed even if there "would have been" refs.
    await orchestrator.delete("a")
    with pytest.raises(TenantNotFound):
        await orchestrator.get("a")


async def test_orchestrator_404_beats_in_use_check(
    orchestrator: TenantOrchestrator,
) -> None:
    """Error precedence: missing tenant gets 404 BEFORE the in-use check
    even runs — operator typo / stale UI should see the most actionable
    error."""

    async def _refcount(tenant_id: str) -> int:
        return 999  # would block delete if reached

    orchestrator.set_user_refcount_provider(_refcount)
    with pytest.raises(TenantNotFound):
        await orchestrator.delete("ghost")


async def test_orchestrator_refuses_to_delete_last_tenant(
    orchestrator: TenantOrchestrator,
) -> None:
    """Business rule #2: a live admin must always have ≥ 1 tenant.

    The 404-vs-409 ordering matters: a missing tenant gets 404 (specific),
    not 409 (which would obscure the missing-record case).
    """
    await orchestrator.create(CreateTenantRequest(tenant_id="only", display_name="Only"))
    with pytest.raises(LastTenantError):
        await orchestrator.delete("only")
    # Still there.
    assert (await orchestrator.get("only")).tenant_id == "only"


async def test_orchestrator_list_sorted_by_created_at_then_id(
    orchestrator: TenantOrchestrator,
) -> None:
    """UI relies on stable ordering: oldest first, ties broken by id.

    This guarantees the seeded ``default`` tenant always sorts first,
    keeping the dropdown predictable for operators.
    """
    import asyncio

    await orchestrator.create(CreateTenantRequest(tenant_id="charlie", display_name="C"))
    await asyncio.sleep(0.01)  # ensure distinct timestamps
    await orchestrator.create(CreateTenantRequest(tenant_id="alpha", display_name="A"))
    await asyncio.sleep(0.01)
    await orchestrator.create(CreateTenantRequest(tenant_id="bravo", display_name="B"))
    listed = await orchestrator.list_all()
    assert [t.tenant_id for t in listed] == ["charlie", "alpha", "bravo"]


# ---- seed_default ----------------------------------------------------------


async def test_seed_default_creates_when_empty(
    orchestrator: TenantOrchestrator,
) -> None:
    created = await seed_default(orchestrator)
    assert created is True
    spec = await orchestrator.get("default")
    assert spec.display_name == "Default"


async def test_seed_default_idempotent_on_repeat(
    orchestrator: TenantOrchestrator,
) -> None:
    """Two calls in a row: first creates, second returns False.

    Critical for admin restart — lifespan calls seed_default on every
    boot, so it MUST be safe to call repeatedly.
    """
    first = await seed_default(orchestrator)
    second = await seed_default(orchestrator)
    assert first is True
    assert second is False


# ---- router (HTTP via ASGITransport) ----------------------------------------
# starlette's TestClient spins up its own event loop, which would conflict
# with the pytest-asyncio loop owning our KVClient connection. We drive the
# ASGI app directly via httpx's in-process transport instead — same trick
# test_devices_router uses.


@pytest.fixture
async def client(orchestrator: TenantOrchestrator) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.state.tenant_orchestrator = orchestrator
    app.include_router(tenants_router, prefix="/api")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", trust_env=False
    ) as c:
        yield c


async def test_http_list_returns_envelope_with_tenants(
    client: httpx.AsyncClient,
) -> None:
    r = await client.get("/api/tenants")
    assert r.status_code == 200
    assert r.json() == {"tenants": []}


async def test_http_create_returns_201_with_spec(client: httpx.AsyncClient) -> None:
    r = await client.post(
        "/api/tenants", json={"tenant_id": "acme", "display_name": "Acme"}
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["tenant_id"] == "acme"
    assert body["display_name"] == "Acme"
    assert "created_at" in body


async def test_http_create_duplicate_returns_409(client: httpx.AsyncClient) -> None:
    await client.post(
        "/api/tenants", json={"tenant_id": "acme", "display_name": "X"}
    )
    r = await client.post(
        "/api/tenants", json={"tenant_id": "acme", "display_name": "Y"}
    )
    assert r.status_code == 409


async def test_http_create_invalid_id_returns_422(
    client: httpx.AsyncClient,
) -> None:
    """Pydantic regex catches bad chars before they reach the orchestrator."""
    r = await client.post(
        "/api/tenants", json={"tenant_id": "bad id with spaces", "display_name": "X"}
    )
    assert r.status_code == 422


async def test_http_get_missing_returns_404(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/tenants/ghost")
    assert r.status_code == 404


async def test_http_update_returns_new_display_name(
    client: httpx.AsyncClient,
) -> None:
    await client.post(
        "/api/tenants", json={"tenant_id": "acme", "display_name": "Old"}
    )
    r = await client.put("/api/tenants/acme", json={"display_name": "New"})
    assert r.status_code == 200
    assert r.json()["display_name"] == "New"


async def test_http_delete_returns_204_then_404(client: httpx.AsyncClient) -> None:
    await client.post("/api/tenants", json={"tenant_id": "a", "display_name": "A"})
    await client.post("/api/tenants", json={"tenant_id": "b", "display_name": "B"})
    r = await client.delete("/api/tenants/a")
    assert r.status_code == 204
    r2 = await client.get("/api/tenants/a")
    assert r2.status_code == 404


async def test_http_delete_last_tenant_returns_409(
    client: httpx.AsyncClient,
) -> None:
    await client.post(
        "/api/tenants", json={"tenant_id": "only", "display_name": "Only"}
    )
    r = await client.delete("/api/tenants/only")
    assert r.status_code == 409
    assert "only tenant" in r.json()["detail"]


async def test_http_503_when_orchestrator_missing() -> None:
    """If registry init failed, the orchestrator slot is None — the router
    returns a clean 503 rather than crashing."""
    app = FastAPI()
    app.state.tenant_orchestrator = None
    app.include_router(tenants_router, prefix="/api")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/api/tenants")
    assert r.status_code == 503
