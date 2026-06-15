"""Tests for admin's Users module (Phase 29.E).

Three layers under test, matching the templates pattern:

  - **MemoryUserClient + UserMetadataRepository**: the two repos.
    MemoryUserClient covered by respx; metadata repo covered by a
    per-test SQLite file.

  - **UserOrchestrator**: status-code translation, compose-from-two-sources,
    create-with-compensation, cascade-on-delete.

  - **Router**: HTTP wiring via httpx.ASGITransport.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import AsyncIterator

import httpx
import pytest
import respx
from fastapi import FastAPI

from eidolon_admin_server.app.registry.schemas.tenant import (
    CreateTenantRequest,
    TenantSpec,
)
from eidolon_admin_server.app.registry.schemas.user import (
    CreateUserRequest,
    SetActiveAgentRequest,
    UpdateUserRequest,
)
from eidolon_admin_server.app.registry.tenants import (
    TenantOrchestrator,
    TenantRepository,
)
from eidolon_admin_server.app.registry.users import (
    MemoryUserClient,
    UserMemoryDown,
    UserMetadataRepository,
    UserOrchestrator,
    router as users_router,
)
from eidolon_admin_server.app.registry.users.orchestrator import (
    TenantNotFoundForUser,
    UserAlreadyExists,
    UserError,
    UserNotFound,
)
from eidolon_admin_server.app.registry.users.repository import (
    MemoryUserUnreachable,
    MemoryUserUpstreamError,
    UserMetadata,
)


MEMORY_URL = "http://memory.test"


# ---- fixtures ---------------------------------------------------------------


@pytest.fixture
async def http_client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as c:
        yield c


@pytest.fixture
async def orchestrator(
    http_client: httpx.AsyncClient,
    tmp_path,
) -> AsyncIterator[UserOrchestrator]:
    """Real tenant repository + SQLite user metadata + respx-mockable
    memory client. The default tenant is seeded so create-user happy path
    doesn't need extra setup."""
    registry_db = tmp_path / "registry.sqlite3"
    tenant_orch = TenantOrchestrator(TenantRepository(registry_db))
    await tenant_orch.create(
        CreateTenantRequest(tenant_id="default", display_name="Default")
    )
    memory_client = MemoryUserClient(http_client, MEMORY_URL)
    metadata_repo = UserMetadataRepository(registry_db)
    yield UserOrchestrator(
        memory_client=memory_client,
        metadata_repo=metadata_repo,
        tenant_orchestrator=tenant_orch,
    )


# ---- metadata repository ----------------------------------------------------


async def test_user_metadata_repository_uses_local_sqlite(tmp_path) -> None:
    repo = UserMetadataRepository(tmp_path / "registry.sqlite3")

    assert await repo.get("alice") is None
    await repo.put(
        "alice",
        UserMetadata(
            tenant_id="default",
            active_agent_id="ag-1",
            display_name="Alice",
        ),
    )

    stored = await repo.get("alice")
    assert stored == UserMetadata(
        tenant_id="default",
        active_agent_id="ag-1",
        display_name="Alice",
    )
    assert await repo.list_all() == {"alice": stored}

    await repo.delete("alice")
    assert await repo.get("alice") is None


# ---- memory wire shape helper ----------------------------------------------


def _memory_user_record(
    *,
    user_id: str = "alice",
    enabled: bool = True,
    worker_running: bool = True,
    mcp_reachable: bool = True,
    palace_initialized: bool = True,
    consolidator: dict | None = None,
) -> dict:
    """Build what memory's /api/admin/users/{id} returns. Keep in sync
    with eidolon_memory/.../user_admin.user_to_view()."""
    if consolidator is None:
        consolidator = {
            "enabled": True,
            "interval_hours": 6.0,
            "window_days": 30,
            "min_drawers": 3,
            "min_confidence": 0.6,
        }
    return {
        "spec": {
            "user_id": user_id,
            "tenant_id": "default",
            "display_name": user_id,
            "enabled": enabled,
            "palace_path": "",
            "consolidator": consolidator,
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
        "health": {
            "worker_running": worker_running,
            "mcp_reachable": mcp_reachable,
            "palace_initialized": palace_initialized,
            "note": "" if enabled else "user disabled (yaml enabled=false)",
        },
        "active_agent_id": None,
        "agent_ids": [],
    }


# ---- orchestrator: list / get ---------------------------------------------


async def test_list_joins_memory_with_admin_metadata(
    orchestrator: UserOrchestrator,
) -> None:
    """Memory says "user alice exists"; admin's metadata store says she's
    in tenant default with active_agent ag-1. The view must combine both."""
    # Pre-populate admin metadata
    await orchestrator._meta.put(
        "alice",
        UserMetadata(
            tenant_id="default", active_agent_id="ag-1", display_name="Alice K."
        ),
    )
    with respx.mock(base_url=MEMORY_URL) as rsx:
        rsx.get("/api/admin/users").mock(
            return_value=httpx.Response(
                200,
                json={"users": [_memory_user_record(user_id="alice")], "memory_available": True},
            )
        )
        views = await orchestrator.list_users()
    assert len(views) == 1
    v = views[0]
    assert v.spec.user_id == "alice"
    assert v.spec.tenant_id == "default"
    assert v.spec.display_name == "Alice K."
    assert v.active_agent_id == "ag-1"
    assert v.health.worker_running is True


async def test_list_decorates_agent_ids_when_provider_wired(
    orchestrator: UserOrchestrator,
) -> None:
    """Phase 29.K: ``UserView.agent_ids`` used to always be []. Now,
    when ``set_agent_ids_provider`` is wired (lifespan does this once
    AgentOrchestrator exists), each user's agents get listed. Pins the
    fix — and that the lookup is per-user, not shared across users."""
    await orchestrator._meta.put(
        "alice", UserMetadata(tenant_id="default", display_name="A")
    )
    await orchestrator._meta.put(
        "bob", UserMetadata(tenant_id="default", display_name="B")
    )

    calls: list[str] = []

    async def fake_provider(user_id: str) -> list[str]:
        calls.append(user_id)
        return {"alice": ["ag-1", "ag-2"], "bob": []}.get(user_id, [])

    orchestrator.set_agent_ids_provider(fake_provider)

    with respx.mock(base_url=MEMORY_URL) as rsx:
        rsx.get("/api/admin/users").mock(
            return_value=httpx.Response(
                200,
                json={
                    "users": [
                        _memory_user_record(user_id="alice"),
                        _memory_user_record(user_id="bob"),
                    ],
                    "memory_available": True,
                },
            )
        )
        views = await orchestrator.list_users()
    by_id = {v.spec.user_id: v for v in views}
    assert by_id["alice"].agent_ids == ["ag-1", "ag-2"]
    assert by_id["bob"].agent_ids == []
    # Lookup invoked once per user, with the correct id.
    assert set(calls) == {"alice", "bob"}


async def test_list_agent_ids_failure_falls_back_to_empty(
    orchestrator: UserOrchestrator,
) -> None:
    """If the provider throws (agent service blip during list), the
    user list still renders — agent_ids drops to [] for the affected
    user. Mirrors the partial-degradation pattern in list_agents."""

    async def broken_provider(user_id: str) -> list[str]:
        raise RuntimeError("agent service down")

    orchestrator.set_agent_ids_provider(broken_provider)

    await orchestrator._meta.put(
        "alice", UserMetadata(tenant_id="default", display_name="A")
    )
    with respx.mock(base_url=MEMORY_URL) as rsx:
        rsx.get("/api/admin/users").mock(
            return_value=httpx.Response(
                200,
                json={"users": [_memory_user_record(user_id="alice")], "memory_available": True},
            )
        )
        views = await orchestrator.list_users()
    assert len(views) == 1
    assert views[0].agent_ids == []


async def test_list_omits_memory_users_without_admin_metadata_orphans(
    orchestrator: UserOrchestrator,
) -> None:
    """A memory user without an admin metadata entry surfaces as a view
    with default fallback (tenant=default, no active_agent). This makes
    "memory has users admin doesn't track" visible to the operator —
    they can either adopt or delete from admin."""
    with respx.mock(base_url=MEMORY_URL) as rsx:
        rsx.get("/api/admin/users").mock(
            return_value=httpx.Response(
                200,
                json={"users": [_memory_user_record(user_id="orphan")], "memory_available": True},
            )
        )
        views = await orchestrator.list_users()
    assert len(views) == 1
    assert views[0].spec.user_id == "orphan"
    assert views[0].spec.tenant_id == "default"  # fallback
    assert views[0].active_agent_id is None


async def test_list_memory_down_translates_to_user_memory_down(
    orchestrator: UserOrchestrator,
) -> None:
    with respx.mock(base_url=MEMORY_URL) as rsx:
        rsx.get("/api/admin/users").mock(side_effect=httpx.ConnectError("down"))
        with pytest.raises(UserMemoryDown):
            await orchestrator.list_users()


async def test_get_404_translates_to_user_not_found(
    orchestrator: UserOrchestrator,
) -> None:
    with respx.mock(base_url=MEMORY_URL) as rsx:
        rsx.get("/api/admin/users/ghost").mock(return_value=httpx.Response(404))
        with pytest.raises(UserNotFound):
            await orchestrator.get_user("ghost")


# ---- orchestrator: create + cross-project invariants ----------------------


async def test_create_user_happy_path(orchestrator: UserOrchestrator) -> None:
    """Memory create + admin metadata write both succeed → view is composed."""
    with respx.mock(base_url=MEMORY_URL) as rsx:
        route = rsx.post("/api/admin/users").mock(
            return_value=httpx.Response(
                201,
                json=_memory_user_record(user_id="alice", enabled=False),
            )
        )
        view = await orchestrator.create_user(
            CreateUserRequest(
                user_id="alice", tenant_id="default", display_name="Alice"
            )
        )
    assert view.spec.user_id == "alice"
    assert view.spec.enabled is False
    assert view.spec.tenant_id == "default"
    assert view.spec.display_name == "Alice"
    # Admin metadata persisted
    meta = await orchestrator._meta.get("alice")
    assert meta is not None
    assert meta.tenant_id == "default"
    assert meta.display_name == "Alice"
    assert json.loads(route.calls.last.request.content)["enabled"] is False


async def test_create_user_with_missing_tenant_returns_409(
    orchestrator: UserOrchestrator,
) -> None:
    """Cross-project invariant: tenant must exist BEFORE assigning users
    to it. Memory create should not even be attempted."""
    with respx.mock(base_url=MEMORY_URL) as rsx:
        # If memory.create gets called, that's a test failure (the
        # tenant-check must short-circuit). We register no route so any
        # call would surface as an unmatched-request error.
        with pytest.raises(TenantNotFoundForUser):
            await orchestrator.create_user(
                CreateUserRequest(
                    user_id="alice",
                    tenant_id="nonexistent",
                    display_name="Alice",
                )
            )


async def test_create_user_409_from_memory_translates(
    orchestrator: UserOrchestrator,
) -> None:
    with respx.mock(base_url=MEMORY_URL) as rsx:
        rsx.post("/api/admin/users").mock(
            return_value=httpx.Response(409, text="already in users.yaml")
        )
        with pytest.raises(UserAlreadyExists):
            await orchestrator.create_user(
                CreateUserRequest(
                    user_id="alice", tenant_id="default", display_name="X"
                )
            )


async def test_create_user_rolls_back_memory_on_admin_metadata_failure(
    orchestrator: UserOrchestrator, monkeypatch
) -> None:
    """The 2-step create's compensation path: memory create succeeds, then
    metadata write fails → orchestrator should DELETE the memory user
    so the two sides don't drift."""

    async def _explode(*_args, **_kwargs):
        raise RuntimeError("simulated kv put failure")

    monkeypatch.setattr(orchestrator._meta, "put", _explode)

    deletes_seen: list[str] = []
    with respx.mock(base_url=MEMORY_URL) as rsx:
        rsx.post("/api/admin/users").mock(
            return_value=httpx.Response(201, json=_memory_user_record(user_id="rolled"))
        )

        # respx delete needs to record AND respond OK
        def _delete_handler(request):
            deletes_seen.append(str(request.url))
            return httpx.Response(200, json={"user_id": "rolled", "deleted": True})

        rsx.delete("/api/admin/users/rolled").mock(side_effect=_delete_handler)

        with pytest.raises(UserError) as exc_info:
            await orchestrator.create_user(
                CreateUserRequest(
                    user_id="rolled", tenant_id="default", display_name="X"
                )
            )

    assert "rolled back" in str(exc_info.value)
    # Verify the rollback actually called memory's delete
    assert any("rolled" in url for url in deletes_seen)


# ---- orchestrator: update / set_active_agent / delete ----------------------


async def test_update_only_changes_admin_owned_fields(
    orchestrator: UserOrchestrator,
) -> None:
    # Seed
    with respx.mock(base_url=MEMORY_URL) as rsx:
        rsx.post("/api/admin/users").mock(
            return_value=httpx.Response(201, json=_memory_user_record(user_id="alice"))
        )
        await orchestrator.create_user(
            CreateUserRequest(
                user_id="alice", tenant_id="default", display_name="Old"
            )
        )
    # Update display_name
    with respx.mock(base_url=MEMORY_URL) as rsx:
        rsx.get("/api/admin/users/alice").mock(
            return_value=httpx.Response(200, json=_memory_user_record(user_id="alice"))
        )
        view = await orchestrator.update_user(
            "alice", UpdateUserRequest(display_name="New")
        )
    assert view.spec.display_name == "New"
    meta = await orchestrator._meta.get("alice")
    assert meta.display_name == "New"


async def test_set_active_agent_persists_to_admin_kv(
    orchestrator: UserOrchestrator,
) -> None:
    # Seed
    with respx.mock(base_url=MEMORY_URL) as rsx:
        rsx.post("/api/admin/users").mock(
            return_value=httpx.Response(201, json=_memory_user_record(user_id="alice"))
        )
        await orchestrator.create_user(
            CreateUserRequest(
                user_id="alice", tenant_id="default", display_name="A"
            )
        )
    with respx.mock(base_url=MEMORY_URL) as rsx:
        rsx.get("/api/admin/users/alice").mock(
            return_value=httpx.Response(200, json=_memory_user_record(user_id="alice"))
        )
        view = await orchestrator.set_active_agent(
            "alice", SetActiveAgentRequest(agent_id="ag-1")
        )
    assert view.active_agent_id == "ag-1"


async def test_delete_user_calls_memory_then_cleans_metadata(
    orchestrator: UserOrchestrator,
) -> None:
    # Pre-populate admin metadata so we can verify it gets cleaned
    await orchestrator._meta.put(
        "alice", UserMetadata(tenant_id="default", display_name="A")
    )
    with respx.mock(base_url=MEMORY_URL) as rsx:
        rsx.delete("/api/admin/users/alice").mock(
            return_value=httpx.Response(
                200,
                json={
                    "user_id": "alice",
                    "deleted": True,
                    "palace_trashed_to": "/tmp/trash/alice_xxx",
                },
            )
        )
        result = await orchestrator.delete_user("alice")
    assert result["deleted"] is True
    assert result["palace_trashed_to"] == "/tmp/trash/alice_xxx"
    # Admin metadata cleaned
    assert await orchestrator._meta.get("alice") is None


async def test_delete_user_deletes_owned_agents_before_memory(
    orchestrator: UserOrchestrator,
) -> None:
    """User delete cascades through agent delete first. The fake agent
    delete result mirrors AgentOrchestrator.delete_agent's envelope."""
    await orchestrator._meta.put(
        "alice", UserMetadata(tenant_id="default", display_name="A")
    )
    calls: list[str] = []

    async def fake_agent_ids(user_id: str) -> list[str]:
        calls.append(f"list:{user_id}")
        return ["ag-1", "ag-2"]

    async def fake_delete_agent(agent_id: str) -> dict:
        calls.append(f"delete-agent:{agent_id}")
        return {
            "agent_id": agent_id,
            "deleted": True,
            "active_agent_cleared_for_users": ["alice"],
            "unbound_devices": [],
        }

    orchestrator.set_agent_ids_provider(fake_agent_ids)
    orchestrator.set_agent_delete_provider(fake_delete_agent)

    with respx.mock(base_url=MEMORY_URL) as rsx:
        rsx.delete("/api/admin/users/alice").mock(
            return_value=httpx.Response(
                200,
                json={
                    "user_id": "alice",
                    "deleted": True,
                    "palace_trashed_to": "/tmp/trash/alice_xxx",
                },
            )
        )
        result = await orchestrator.delete_user("alice")

    assert calls == ["list:alice", "delete-agent:ag-1", "delete-agent:ag-2"]
    assert result["deleted_agents"] == ["ag-1", "ag-2"]
    assert await orchestrator._meta.get("alice") is None


async def test_delete_user_aborts_if_owned_agent_delete_fails(
    orchestrator: UserOrchestrator,
) -> None:
    """If related data cannot be removed, user delete must not proceed
    to memory. That prevents orphaning agents behind a deleted user."""
    await orchestrator._meta.put(
        "alice", UserMetadata(tenant_id="default", display_name="A")
    )

    async def fake_agent_ids(user_id: str) -> list[str]:
        return ["ag-1"]

    async def broken_delete_agent(agent_id: str) -> dict:
        raise RuntimeError("agent project down")

    orchestrator.set_agent_ids_provider(fake_agent_ids)
    orchestrator.set_agent_delete_provider(broken_delete_agent)

    with respx.mock(base_url=MEMORY_URL):
        with pytest.raises(UserError, match="failed to delete owned agent"):
            await orchestrator.delete_user("alice")

    assert await orchestrator._meta.get("alice") is not None


async def test_delete_user_propagates_memory_404(
    orchestrator: UserOrchestrator,
) -> None:
    with respx.mock(base_url=MEMORY_URL) as rsx:
        rsx.delete("/api/admin/users/ghost").mock(
            return_value=httpx.Response(404)
        )
        with pytest.raises(UserNotFound):
            await orchestrator.delete_user("ghost")


# ---- router (HTTP wire) ----------------------------------------------------


@pytest.fixture
async def client(
    orchestrator: UserOrchestrator,
) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.state.user_orchestrator = orchestrator
    app.include_router(users_router, prefix="/api")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", trust_env=False
    ) as c:
        yield c


async def test_http_list_envelope_when_memory_up(
    client: httpx.AsyncClient,
) -> None:
    with respx.mock(base_url=MEMORY_URL) as rsx:
        rsx.get("/api/admin/users").mock(
            return_value=httpx.Response(
                200, json={"users": [], "memory_available": True}
            )
        )
        r = await client.get("/api/users")
    assert r.status_code == 200
    assert r.json() == {"users": [], "memory_available": True}


async def test_http_list_envelope_when_memory_down(
    client: httpx.AsyncClient,
) -> None:
    with respx.mock(base_url=MEMORY_URL) as rsx:
        rsx.get("/api/admin/users").mock(side_effect=httpx.ConnectError("down"))
        r = await client.get("/api/users")
    assert r.status_code == 200
    body = r.json()
    assert body["users"] == []
    assert body["memory_available"] is False


async def test_http_create_201(client: httpx.AsyncClient) -> None:
    with respx.mock(base_url=MEMORY_URL) as rsx:
        rsx.post("/api/admin/users").mock(
            return_value=httpx.Response(201, json=_memory_user_record(user_id="alice"))
        )
        r = await client.post(
            "/api/users",
            json={"user_id": "alice", "display_name": "Alice"},
        )
    assert r.status_code == 201, r.text
    assert r.json()["spec"]["user_id"] == "alice"


async def test_http_create_with_missing_tenant_returns_409(
    client: httpx.AsyncClient,
) -> None:
    r = await client.post(
        "/api/users",
        json={
            "user_id": "alice",
            "tenant_id": "ghost",
            "display_name": "x",
        },
    )
    assert r.status_code == 409
    assert "tenant" in r.json()["detail"]


async def test_http_503_when_orchestrator_missing() -> None:
    app = FastAPI()
    app.state.user_orchestrator = None
    app.include_router(users_router, prefix="/api")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/api/users")
    assert r.status_code == 503


# ---- cross-module cascade (29.E.1) -----------------------------------------


async def test_count_users_for_tenant_reflects_admin_metadata(
    orchestrator: UserOrchestrator,
) -> None:
    """The cascade hook that TenantOrchestrator wires in. Verifies
    admin's metadata store is the authoritative source for tenant↔user mapping."""
    # Default tenant exists from the orchestrator fixture. Add 2 users
    # to it + 1 to a different tenant.
    await orchestrator._meta.put("u1", UserMetadata(tenant_id="default"))
    await orchestrator._meta.put("u2", UserMetadata(tenant_id="default"))
    await orchestrator._meta.put("u3", UserMetadata(tenant_id="other"))

    assert await orchestrator.count_users_for_tenant("default") == 2
    assert await orchestrator.count_users_for_tenant("other") == 1
    assert await orchestrator.count_users_for_tenant("none") == 0


async def test_tenant_delete_through_lifespan_wiring_refuses_when_users_present(
    orchestrator: UserOrchestrator,
) -> None:
    """End-to-end verification of the 29.E.1 wiring: TenantOrchestrator
    has UserOrchestrator's refcount method installed, so a delete
    attempt against a tenant with users is refused.

    This is what main.py's lifespan does at startup. We replicate it
    here so the cross-module contract is pinned in tests too — not just
    in the manual wiring of the running app.
    """
    from eidolon_admin_server.app.registry.tenants.orchestrator import (
        TenantInUse,
    )

    tenant_orch = orchestrator._tenants
    # Wire as main.py does
    tenant_orch.set_user_refcount_provider(orchestrator.count_users_for_tenant)

    # Add a user to default
    await orchestrator._meta.put("alice", UserMetadata(tenant_id="default"))

    # Create a second tenant (otherwise last-tenant guard fires first)
    from eidolon_admin_server.app.registry.schemas.tenant import (
        CreateTenantRequest,
    )
    await tenant_orch.create(
        CreateTenantRequest(tenant_id="other", display_name="Other")
    )

    # Now delete should refuse because default has 1 user.
    with pytest.raises(TenantInUse):
        await tenant_orch.delete("default")
