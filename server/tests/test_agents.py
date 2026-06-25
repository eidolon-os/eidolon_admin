"""Tests for admin's Agents module (Phase 29.F).

Temporary SQLite registry + respx-mocked agent project. The
orchestrator's cross-project compose is exercised against:

  - real UserOrchestrator (with respx-mocked memory)
  - real AgentMetadataRepository (SQLite-backed)
  - a fake template_exists_check callable
  - respx-mocked AgentProjectClient
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import AsyncIterator

import httpx
import pytest
import respx
from fastapi import FastAPI

from eidolon_sdk.adapters.registry_sqlite import (
    AgentMetadataRepository as SqliteAgentMetadataRepository,
    RegistrySqliteStore,
    TenantRepository,
    UserRepository,
)
from eidolon_sdk.biz.registry.models import UserRegistryRecord

from eidolon_admin_server.app.registry.agents import (
    AgentMetadataRepository,
    AgentNotFound,
    AgentOrchestrator,
    AgentProjectClient,
    router as agents_router,
)
from eidolon_admin_server.app.registry.agents.orchestrator import (
    AgentBadRequest,
    AgentError,
)
from eidolon_admin_server.app.registry.agents.repository import AgentMetadata
from eidolon_admin_server.app.registry.schemas.agent import CreateAgentRequest
from eidolon_admin_server.app.registry.schemas.tenant import CreateTenantRequest
from eidolon_admin_server.app.registry.tenants import (
    TenantOrchestrator,
)
from eidolon_admin_server.app.registry.users import (
    MemoryUserClient,
    UserOrchestrator,
)


MEMORY_URL = "http://memory.test"
AGENT_URL = "http://agent.test"


def _user_record(user_id: str, **updates) -> UserRegistryRecord:
    data = {
        "user_id": user_id,
        "tenant_id": "default",
        "active_agent_id": None,
        "display_name": "",
        "enabled": True,
        "palace_path": "",
        "memory_port": 0,
        "created_at": "",
    }
    data.update(updates)
    return UserRegistryRecord(**data)


# ---- fixtures ---------------------------------------------------------------


@pytest.fixture
async def http_client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as c:
        yield c


def _memory_user_record(user_id: str = "alice") -> dict:
    return {
        "spec": {
            # memory keys its records by memory_space_id, not bare user_id.
            "user_id": f"default.{user_id}.default",
            "tenant_id": "default",
            "display_name": user_id,
            "palace_path": "",
            "consolidator": {
                "enabled": True, "interval_hours": 6.0, "window_days": 30,
                "min_drawers": 3, "min_confidence": 0.6,
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
        "health": {
            "worker_running": True, "mcp_reachable": True,
            "palace_initialized": True, "note": "",
        },
        "active_agent_id": None,
        "agent_ids": [],
    }


def _persona_instance(instance_id: str = "ag_xxx", user_id: str = "alice") -> dict:
    """Mimic agent's PersonaInstance JSON shape."""
    now = datetime.now(timezone.utc).isoformat()
    return {
        "instance_id": instance_id,
        "tenant_id": "default",
        "user_id": user_id,
        "origin_template_id": "caretaker_jiezhi",
        "origin_template_revision": 1,
        "overlay_version": 1,
        "created_at": now,
        "updated_at": now,
        "metadata": {
            "template_id": "caretaker_jiezhi",
            "template_revision": 1,
            "archetype": "caretaker",
            "name": "Caretaker",
            "description": "",
        },
        "identity_core": {"base_pronouns": "她"},
        "behavioral_knobs": {
            "warmth": {"current": 0.6, "min": 0.0, "max": 1.0,
                       "step_limit": 0.05, "cooldown_hours": 0,
                       "last_changed_at": None},
        },
        "evolution_state": {"applied_rule_timestamps": {}, "last_event_summary": ""},
    }


@pytest.fixture
async def orchestrator(
    http_client: httpx.AsyncClient,
    tmp_path,
) -> AsyncIterator[AgentOrchestrator]:
    # Build all the underpinnings the agent orchestrator depends on.
    registry_db = tmp_path / "registry.sqlite3"
    store = RegistrySqliteStore(registry_db)
    tenant_orch = TenantOrchestrator(TenantRepository(store))
    await tenant_orch.create(
        CreateTenantRequest(tenant_id="default", display_name="Default")
    )
    memory_client = MemoryUserClient(http_client, MEMORY_URL)
    user_repo = UserRepository(store)
    user_orch = UserOrchestrator(
        memory_client=memory_client,
        metadata_repo=user_repo,
        tenant_orchestrator=tenant_orch,
    )
    await user_repo.put(_user_record("alice", display_name="Alice"))
    tenant_orch.set_user_refcount_provider(user_orch.count_users_for_tenant)

    # Stub template-exists check — defaults to True for "caretaker_jiezhi",
    # False otherwise. Individual tests can override.
    known_templates = {"caretaker_jiezhi"}

    async def _template_exists(template_id: str) -> bool:
        return template_id in known_templates

    agent_client = AgentProjectClient(http_client, AGENT_URL)
    agent_repo = AgentMetadataRepository(SqliteAgentMetadataRepository(store))
    orch = AgentOrchestrator(
        agent_client=agent_client,
        metadata_repo=agent_repo,
        user_orchestrator=user_orch,
        template_exists_check=_template_exists,
    )
    # expose helpers for tests
    orch._test_known_templates = known_templates  # type: ignore[attr-defined]
    orch._test_user_orch = user_orch  # type: ignore[attr-defined]
    yield orch
    await store.dispose()


# ---- list / get -----------------------------------------------------------


async def test_list_empty(orchestrator: AgentOrchestrator) -> None:
    assert await orchestrator.list_agents() == []


async def test_list_filters_by_user(orchestrator: AgentOrchestrator) -> None:
    """Pre-seed metadata: 2 agents for alice, 1 for bob. Filter works."""
    base = datetime.now(timezone.utc).isoformat()
    await orchestrator._test_user_orch._meta.put(
        _user_record("bob", display_name="Bob")
    )
    await orchestrator._meta.put(
        "ag-1",
        AgentMetadata(
            tenant_id="default", user_id="alice", template_id="t",
            template_revision=1, display_name="A1", created_at=base,
        ),
    )
    await orchestrator._meta.put(
        "ag-2",
        AgentMetadata(
            tenant_id="default", user_id="alice", template_id="t",
            template_revision=1, display_name="A2", created_at=base,
        ),
    )
    await orchestrator._meta.put(
        "ag-3",
        AgentMetadata(
            tenant_id="default", user_id="bob", template_id="t",
            template_revision=1, display_name="B1", created_at=base,
        ),
    )
    with respx.mock(base_url=MEMORY_URL) as rsx:
        rsx.get("/api/admin/users/default.alice.default").mock(
            return_value=httpx.Response(200, json=_memory_user_record("alice"))
        )
        rsx.get("/api/admin/users/default.bob.default").mock(
            return_value=httpx.Response(200, json=_memory_user_record("bob"))
        )
        all_agents = await orchestrator.list_agents()
    assert {a.agent_id for a in all_agents} == {"ag-1", "ag-2", "ag-3"}

    with respx.mock(base_url=MEMORY_URL) as rsx:
        rsx.get("/api/admin/users/default.alice.default").mock(
            return_value=httpx.Response(200, json=_memory_user_record("alice"))
        )
        alice_agents = await orchestrator.list_agents(user_id="alice")
    assert {a.agent_id for a in alice_agents} == {"ag-1", "ag-2"}


async def test_get_missing_raises_404(orchestrator: AgentOrchestrator) -> None:
    with pytest.raises(AgentNotFound):
        await orchestrator.get_agent("ghost")


# ---- create cross-project invariants -------------------------------------


async def test_create_with_missing_user_returns_bad_request(
    orchestrator: AgentOrchestrator,
) -> None:
    """User MUST exist in the admin registry before an agent can be created.
    Returns AgentBadRequest (400) — the request body is wrong, not
    the endpoint."""
    with pytest.raises(AgentBadRequest, match="user 'ghost' not found") as exc_info:
        await orchestrator.create_agent(
            CreateAgentRequest(user_id="ghost", template_id="caretaker_jiezhi")
        )
    assert exc_info.value.status_code == 400


async def test_create_with_missing_template_returns_bad_request(
    orchestrator: AgentOrchestrator,
) -> None:
    """Template MUST exist (per template_exists_check)."""
    with respx.mock(base_url=MEMORY_URL) as rsx:
        rsx.get("/api/admin/users/default.alice.default").mock(
            return_value=httpx.Response(200, json=_memory_user_record("alice"))
        )
        with pytest.raises(AgentBadRequest, match="template 'unknown' not found") as exc_info:
            await orchestrator.create_agent(
                CreateAgentRequest(user_id="alice", template_id="unknown")
            )
        assert exc_info.value.status_code == 400


async def test_create_happy_path(orchestrator: AgentOrchestrator) -> None:
    """End-to-end: user check OK, template check OK, agent project responds,
    admin metadata is written. By default it does not become active."""
    with respx.mock() as rsx:
        rsx.get(f"{MEMORY_URL}/api/admin/users/default.alice.default").mock(
            return_value=httpx.Response(200, json=_memory_user_record("alice"))
        )
        # The agent_id is generated inside the orchestrator (uuid). Match
        # against the path pattern by passing a regex via respx.
        rsx.post(f"{AGENT_URL}/api/admin/personas/instances").mock(
            return_value=httpx.Response(201, json=_persona_instance(user_id="alice"))
        )
        ref = await orchestrator.create_agent(
            CreateAgentRequest(
                user_id="alice",
                template_id="caretaker_jiezhi",
                display_name="My First",
            )
        )
    assert ref.user_id == "alice"
    assert ref.template_id == "caretaker_jiezhi"
    assert ref.display_name == "My First"
    assert ref.is_active_for_user is False

    # admin metadata persisted
    stored = await orchestrator._meta.get(ref.agent_id)
    assert stored is not None
    assert stored.user_id == "alice"
    assert stored.template_id == "caretaker_jiezhi"

    # user.active_agent_id stays untouched
    user_meta = await orchestrator._test_user_orch._meta.get("alice")
    assert user_meta is None or user_meta.active_agent_id is None


async def test_create_can_explicitly_set_active(
    orchestrator: AgentOrchestrator,
) -> None:
    with respx.mock() as rsx:
        rsx.get(f"{MEMORY_URL}/api/admin/users/default.alice.default").mock(
            return_value=httpx.Response(200, json=_memory_user_record("alice"))
        )
        rsx.post(f"{AGENT_URL}/api/admin/personas/instances").mock(
            return_value=httpx.Response(201, json=_persona_instance(user_id="alice"))
        )
        rsx.get(f"{MEMORY_URL}/api/admin/users/default.alice.default").mock(
            return_value=httpx.Response(200, json=_memory_user_record("alice"))
        )
        ref = await orchestrator.create_agent(
            CreateAgentRequest(
                user_id="alice",
                template_id="caretaker_jiezhi",
                set_active=True,
            )
        )

    assert ref.is_active_for_user is True
    user_meta = await orchestrator._test_user_orch._meta.get("alice")
    assert user_meta is not None
    assert user_meta.active_agent_id == ref.agent_id


async def test_create_rolls_back_persona_on_metadata_failure(
    orchestrator: AgentOrchestrator, monkeypatch,
) -> None:
    """Step 4 (KV write) fails → step 3 rolled back (agent.delete_instance
    called) so the agent project doesn't have an orphan persona."""

    async def _explode(*_args, **_kwargs):
        raise RuntimeError("simulated KV failure")

    monkeypatch.setattr(orchestrator._meta, "put", _explode)

    deletes_seen: list[str] = []

    with respx.mock() as rsx:
        rsx.get(f"{MEMORY_URL}/api/admin/users/default.alice.default").mock(
            return_value=httpx.Response(200, json=_memory_user_record("alice"))
        )
        rsx.post(f"{AGENT_URL}/api/admin/personas/instances").mock(
            return_value=httpx.Response(201, json=_persona_instance(user_id="alice"))
        )

        def _delete_handler(req):
            # capture the rollback DELETE
            deletes_seen.append(str(req.url))
            return httpx.Response(200, json={"deleted": "yes"})

        # Use a regex matcher — the agent_id is freshly minted inside
        # orchestrator.create_agent so we don't know it ahead of time.
        rsx.delete(
            url__regex=r"^http://agent\.test/api/admin/personas/instances/.+"
        ).mock(side_effect=_delete_handler)

        with pytest.raises(AgentError) as exc_info:
            await orchestrator.create_agent(
                CreateAgentRequest(user_id="alice", template_id="caretaker_jiezhi")
            )

    assert "rolled back" in str(exc_info.value)
    # The rollback fired
    assert any("/personas/instances/" in u for u in deletes_seen)


# ---- delete cascade -------------------------------------------------------


async def test_delete_cascades_active_agent_clear(
    orchestrator: AgentOrchestrator,
) -> None:
    """Deleting an agent that's marked as some user's active_agent should
    clear that reference."""
    # Seed: user alice with active_agent='ag-todelete', metadata for it
    base = datetime.now(timezone.utc).isoformat()
    await orchestrator._test_user_orch._meta.put(
        _user_record("alice", active_agent_id="ag-todelete", display_name="A"),
    )
    await orchestrator._meta.put(
        "ag-todelete",
        AgentMetadata(
            tenant_id="default", user_id="alice", template_id="t",
            template_revision=1, display_name="X", created_at=base,
        ),
    )

    with respx.mock() as rsx:
        # Agent project DELETE succeeds.
        rsx.delete(
            f"{AGENT_URL}/api/admin/personas/instances/default/alice/ag-todelete"
        ).mock(return_value=httpx.Response(200, json={"deleted": "ag-todelete"}))
        result = await orchestrator.delete_agent("ag-todelete")

    assert result["deleted"] is True
    assert "alice" in result["active_agent_cleared_for_users"]
    # Admin metadata gone
    assert await orchestrator._meta.get("ag-todelete") is None
    # User's active_agent cleared
    user_meta = await orchestrator._test_user_orch._meta.get("alice")
    assert user_meta is not None
    assert user_meta.active_agent_id is None


async def test_delete_missing_returns_404(
    orchestrator: AgentOrchestrator,
) -> None:
    with pytest.raises(AgentNotFound):
        await orchestrator.delete_agent("ghost")


# ---- 29.H: no silent fallback on user lookup ------------------------------


async def test_list_tolerates_memory_down_with_warning(
    orchestrator: AgentOrchestrator,
) -> None:
    """29.H: list view stays usable when memory is transiently unreachable.

    Differentiation:
      - UserNotFound (genuine "no user")    → silent is_active=None
      - UserMemoryDown (transient infra)    → logged + is_active=None
                                              (list still renders)
      - other exceptions (real bugs)        → propagate

    This is the narrower-than-original silent-fallback handling: we
    avoid surprising the operator with errors in the LIST view (which
    is read-only) but we no longer swallow programming bugs.
    """
    base = datetime.now(timezone.utc).isoformat()
    await orchestrator._meta.put(
        "ag-1",
        AgentMetadata(
            tenant_id="default", user_id="alice", template_id="t",
            template_revision=1, display_name="A", created_at=base,
        ),
    )

    with respx.mock(base_url=MEMORY_URL) as rsx:
        # ConnectError simulates "memory down" → maps to UserMemoryDown
        # in the user orchestrator's translation layer.
        rsx.get("/api/admin/users/default.alice.default").mock(
            side_effect=httpx.ConnectError("memory down")
        )
        agents = await orchestrator.list_agents()
    # List succeeded; the affected agent shows is_active=False
    assert len(agents) == 1
    assert agents[0].is_active_for_user is False


async def test_list_propagates_real_errors(
    orchestrator: AgentOrchestrator, monkeypatch,
) -> None:
    """If user_orchestrator.get_user raises a *non*-UserError exception
    (e.g. a real programming bug), list_agents must propagate it. The
    old `except Exception: return None` masked these."""
    base = datetime.now(timezone.utc).isoformat()
    await orchestrator._meta.put(
        "ag-1",
        AgentMetadata(
            tenant_id="default", user_id="alice", template_id="t",
            template_revision=1, display_name="A", created_at=base,
        ),
    )

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("simulated programming bug")

    monkeypatch.setattr(orchestrator._users, "get_user", _boom)
    with pytest.raises(RuntimeError, match="simulated programming bug"):
        await orchestrator.list_agents()


async def test_delete_treats_agent_project_404_as_already_gone(
    orchestrator: AgentOrchestrator,
) -> None:
    """If admin's registry has the agent but agent project says 404 (drift),
    DELETE should still clean admin metadata — DELETE is idempotent."""
    base = datetime.now(timezone.utc).isoformat()
    await orchestrator._meta.put(
        "ag-drift",
        AgentMetadata(
            tenant_id="default", user_id="alice", template_id="t",
            template_revision=1, display_name="X", created_at=base,
        ),
    )

    with respx.mock() as rsx:
        rsx.delete(
            f"{AGENT_URL}/api/admin/personas/instances/default/alice/ag-drift"
        ).mock(return_value=httpx.Response(404))
        result = await orchestrator.delete_agent("ag-drift")

    assert result["deleted"] is True
    assert await orchestrator._meta.get("ag-drift") is None  # cleaned


# ---- router (HTTP) -------------------------------------------------------


@pytest.fixture
async def client(
    orchestrator: AgentOrchestrator,
) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.state.agent_orchestrator = orchestrator
    app.include_router(agents_router, prefix="/api")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", trust_env=False
    ) as c:
        yield c


async def test_http_list_envelope(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/agents")
    assert r.status_code == 200
    assert r.json() == {"agents": [], "upstream_available": True}


async def test_http_get_missing_returns_404(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/agents/ghost")
    assert r.status_code == 404


async def test_http_503_when_orchestrator_missing() -> None:
    app = FastAPI()
    app.state.agent_orchestrator = None
    app.include_router(agents_router, prefix="/api")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/api/agents")
    assert r.status_code == 503
