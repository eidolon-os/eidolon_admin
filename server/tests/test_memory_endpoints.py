"""Tests for memory read endpoints (Phase 13).

We mock the per-request MCP call_tool / list_tools functions — spinning a real
MCP server in unit tests is overkill, and the per-router logic is mostly
argument shaping + response un-wrapping which is what we actually want to test.
"""
from __future__ import annotations

import sqlite3
import textwrap
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from eidolon_admin_server.app.main import create_app
from eidolon_admin_server.app.settings import (
    AdminBindConfig,
    GatewayConfig,
    Settings,
)


pytestmark = pytest.mark.asyncio


@pytest.fixture
def users_yaml(tmp_path):
    p = tmp_path / "users.yaml"
    p.write_text(textwrap.dedent("""\
        users:
          - id: alice
            port: 8030
            enabled: true
            palace_path: ''
          - id: bob
            port: 8031
            enabled: false
            palace_path: ''
    """))
    return p


@pytest.fixture
def registry_db(tmp_path):
    db_path = tmp_path / "registry.sqlite3"
    conn = sqlite3.connect(db_path)
    with conn:
        conn.execute(
            """
            CREATE TABLE users (
                user_id TEXT PRIMARY KEY,
                enabled INTEGER NOT NULL,
                palace_path TEXT NOT NULL DEFAULT '',
                memory_port INTEGER NOT NULL DEFAULT 0,
                consolidator_enabled INTEGER NOT NULL DEFAULT 1,
                consolidator_interval_hours REAL NOT NULL DEFAULT 6.0,
                consolidator_window_days INTEGER NOT NULL DEFAULT 30,
                consolidator_min_drawers INTEGER NOT NULL DEFAULT 3,
                consolidator_min_confidence REAL NOT NULL DEFAULT 0.6
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO users (
                user_id, enabled, palace_path, memory_port,
                consolidator_enabled, consolidator_interval_hours,
                consolidator_window_days, consolidator_min_drawers,
                consolidator_min_confidence
            ) VALUES (?, ?, '', ?, 1, 6.0, 30, 3, 0.6)
            """,
            [("alice", 1, 8030), ("bob", 0, 8031)],
        )
    conn.close()
    return db_path


@pytest.fixture
def app(tmp_path, users_yaml, registry_db, monkeypatch):
    monkeypatch.setenv("EIDOLON_MEMORY_USERS_YAML", str(users_yaml))
    monkeypatch.setenv("EIDOLON_ADMIN_REGISTRY_DB_PATH", str(registry_db))
    settings = Settings(
        services_file=tmp_path / "svc.yaml",
        supervisor_socket=tmp_path / "missing.sock",
        supervisor_available_dir=tmp_path,
        supervisor_enabled_dir=tmp_path,
    )
    (tmp_path / "svc.yaml").write_text("services: []\n")
    return create_app(
        GatewayConfig(admin=AdminBindConfig(cors_origins=[]), services=[]),
        settings=settings,
    )


async def _http(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw")


# -- users --------------------------------------------------------------------


async def test_users_list(app, users_yaml):
    with patch(
        "eidolon_admin_server.app.memory.routers.users.probe_reachable",
        new=AsyncMock(return_value=True),
    ), patch(
        "eidolon_admin_server.app.memory.routers.users.call_tool",
        new=AsyncMock(return_value={"palace_initialized": True, "ready": True}),
    ):
        async with await _http(app) as ac:
            resp = await ac.get("/api/memory/users")

    assert resp.status_code == 200
    data = resp.json()
    assert data["users_file"].endswith("registry.sqlite3")
    assert [u["user_id"] for u in data["users"]] == ["alice", "bob"]
    alice = data["users"][0]
    assert alice["enabled"] is True
    assert alice["agent_reachable"] is True
    assert alice["palace_initialized"] is True
    assert alice["mcp_http_url"].endswith(":8030/mcp")
    # bob is disabled — no probe attempted.
    assert data["users"][1]["agent_reachable"] is False


# -- memories: search / list --------------------------------------------------


async def test_memories_search(app):
    fake = {"records": [{"key": "k1", "value": "hello", "metadata": {"similarity": 0.91}}]}
    with patch(
        "eidolon_admin_server.app.memory.routers.memories.call_tool",
        new=AsyncMock(return_value=fake),
    ) as mock:
        async with await _http(app) as ac:
            resp = await ac.get(
                "/api/memory/memories/search",
                params={"user_id": "alice", "query": "hi", "top_k": 3},
            )

    assert resp.status_code == 200
    assert resp.json()["records"] == fake["records"]
    mock.assert_awaited_once_with(
        "alice", "eidolon_memory_search", {"query": "hi", "top_k": 3}
    )


async def test_memories_list_returns_total_hint(app):
    fake = {"records": [{"key": "k1"}, {"key": "k2"}], "total_hint": 42}
    with patch(
        "eidolon_admin_server.app.memory.routers.memories.call_tool",
        new=AsyncMock(return_value=fake),
    ):
        async with await _http(app) as ac:
            resp = await ac.get("/api/memory/memories", params={"user_id": "alice"})

    data = resp.json()
    assert data["total_hint"] == 42
    assert len(data["records"]) == 2


async def test_memories_list_tolerates_list_payload(app):
    """Some tool versions return a bare list — endpoint should still cope."""
    fake = [{"key": "k1"}, {"key": "k2"}]
    with patch(
        "eidolon_admin_server.app.memory.routers.memories.call_tool",
        new=AsyncMock(return_value=fake),
    ):
        async with await _http(app) as ac:
            resp = await ac.get("/api/memory/memories", params={"user_id": "alice"})

    data = resp.json()
    assert data["records"] == fake
    assert data["total_hint"] == 2


# -- hierarchy ----------------------------------------------------------------


async def test_hierarchy_passthrough(app):
    fake = {"palace_path": "/x", "layers": [{"level": 1, "title": "Palace"}]}
    with patch(
        "eidolon_admin_server.app.memory.routers.hierarchy.call_tool",
        new=AsyncMock(return_value=fake),
    ):
        async with await _http(app) as ac:
            resp = await ac.get("/api/memory/hierarchy", params={"user_id": "alice"})

    assert resp.json()["data"] == fake


# -- graph --------------------------------------------------------------------


async def test_graph_knowledge(app):
    """kg_snapshot returns {stats, triples}; router projects to {nodes, edges}."""
    fake = {
        "stats": {"entities": 2, "triples_total": 2, "triples_active": 1, "triples_invalidated": 1},
        "triples": [
            {"subject": "alice", "predicate": "knows", "object": "bob"},
            {"subject": "alice", "predicate": "lives_in", "object": "place:北京", "valid_to": "2026-01-01T00:00:00Z"},
        ],
    }
    with patch(
        "eidolon_admin_server.app.memory.routers.graph.call_tool",
        new=AsyncMock(return_value=fake),
    ):
        async with await _http(app) as ac:
            resp = await ac.get(
                "/api/memory/graph/knowledge",
                params={"user_id": "alice", "max_triples": 50},
            )

    data = resp.json()
    # 3 entities: alice, bob, place:北京
    node_ids = {n["id"] for n in data["nodes"]}
    assert node_ids == {"alice", "bob", "place:北京"}
    # entity_type parsed from "place:" prefix
    by_id = {n["id"]: n for n in data["nodes"]}
    assert by_id["place:北京"]["entity_type"] == "place"
    # 2 edges
    assert len(data["edges"]) == 2
    # The second edge is ended (valid_to set), reflected as current=false
    ended = next(e for e in data["edges"] if e["label"] == "lives_in")
    assert ended["current"] is False
    # reason summarises stats
    assert "triples" in data["reason"]


async def test_graph_palace(app):
    fake = {
        "available": True,
        "palace_path": "/x",
        "nodes": [],
        "edges": [],
        "capped": True,
        "reason": "limit reached",
    }
    with patch(
        "eidolon_admin_server.app.memory.routers.graph.call_tool",
        new=AsyncMock(return_value=fake),
    ):
        async with await _http(app) as ac:
            resp = await ac.get("/api/memory/graph/palace", params={"user_id": "alice"})

    assert resp.json()["capped"] is True


# -- kg -----------------------------------------------------------------------


async def test_kg_predicates(app):
    fake = {"predicates": ["likes", "hates"], "sensitive": ["health_status"]}
    with patch(
        "eidolon_admin_server.app.memory.routers.kg.call_tool",
        new=AsyncMock(return_value=fake),
    ):
        async with await _http(app) as ac:
            resp = await ac.get("/api/memory/kg/predicates", params={"user_id": "alice"})

    assert resp.json() == fake


async def test_kg_stats(app):
    fake = {"entities": 12, "triples_total": 50, "triples_active": 40, "triples_invalidated": 10}
    with patch(
        "eidolon_admin_server.app.memory.routers.kg.call_tool",
        new=AsyncMock(return_value=fake),
    ):
        async with await _http(app) as ac:
            resp = await ac.get("/api/memory/kg/stats", params={"user_id": "alice"})

    assert resp.json() == fake


async def test_kg_entity(app):
    fake = {
        "triples": [
            {"subject": "alice", "predicate": "likes", "object": "tea"},
        ]
    }
    with patch(
        "eidolon_admin_server.app.memory.routers.kg.call_tool",
        new=AsyncMock(return_value=fake),
    ) as mock:
        async with await _http(app) as ac:
            resp = await ac.get(
                "/api/memory/kg/entity/alice",
                params={"user_id": "alice", "direction": "both"},
            )

    data = resp.json()
    assert data["entity"] == "alice"
    assert data["triples"][0]["object"] == "tea"
    # direction passed through
    args = mock.await_args.args
    assert args[1] == "eidolon_memory_kg_query_entity"
    assert args[2]["direction"] == "both"


async def test_kg_timeline(app):
    fake = {"triples": [{"subject": "alice", "predicate": "knew", "object": "bob"}]}
    with patch(
        "eidolon_admin_server.app.memory.routers.kg.call_tool",
        new=AsyncMock(return_value=fake),
    ):
        async with await _http(app) as ac:
            resp = await ac.get(
                "/api/memory/kg/timeline",
                params={"user_id": "alice", "entity_name": "alice", "limit": 50},
            )

    assert len(resp.json()["triples"]) == 1


# -- recall -------------------------------------------------------------------


async def test_recall(app):
    fake = {
        "context": "Some synthesized context.",
        "kg_triples": [{"subject": "alice", "predicate": "likes", "object": "tea"}],
        "records": [{"key": "k1", "value": "..."}],
    }
    with patch(
        "eidolon_admin_server.app.memory.routers.recall.call_tool",
        new=AsyncMock(return_value=fake),
    ) as mock:
        async with await _http(app) as ac:
            resp = await ac.post(
                "/api/memory/recall",
                params={"user_id": "alice"},
                json={"query": "what does alice like?", "top_k": 3},
            )

    data = resp.json()
    assert data["context"] == fake["context"]
    assert len(data["kg_triples"]) == 1
    args = mock.await_args.args
    assert args[0] == "alice"
    assert args[1] == "eidolon_memory_recall_context"
    assert args[2]["query"] == "what does alice like?"


# -- mcp tools ----------------------------------------------------------------


async def test_mcp_tools(app):
    fake = [
        {"name": "eidolon_memory_search", "description": "vector search", "input_schema": {"type": "object"}},
        {"name": "eidolon_memory_status", "description": "agent status", "input_schema": {}},
    ]
    with patch(
        "eidolon_admin_server.app.memory.routers.mcp_tools.list_tools",
        new=AsyncMock(return_value=fake),
    ):
        async with await _http(app) as ac:
            resp = await ac.get("/api/memory/mcp/tools", params={"user_id": "alice"})

    data = resp.json()
    assert data["count"] == 2
    assert data["tools"][0]["name"] == "eidolon_memory_search"


# -- errors -------------------------------------------------------------------


# -- writes -------------------------------------------------------------------


async def test_create_memory_publishes_turn(app):
    publisher = AsyncMock()
    publisher.publish_turn = AsyncMock(return_value="turn-abc123")
    app.state.memory_publisher = publisher

    async with await _http(app) as ac:
        resp = await ac.post(
            "/api/memory/memories",
            json={
                "user_id": "alice",
                "wing": "Wing_Profile",
                "room": "profile_core",
                "text": "Alice likes tea.",
                "metadata": {"src": "test"},
            },
        )

    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "accepted"
    assert "turn-abc123" in body["detail"]

    publisher.publish_turn.assert_awaited_once()
    kwargs = publisher.publish_turn.await_args.kwargs
    assert kwargs["user_id"] == "alice"
    assert kwargs["user_text"] == "Alice likes tea."
    assert kwargs["metadata"]["wing"] == "Wing_Profile"
    assert kwargs["metadata"]["src"] == "test"


async def test_create_memory_nats_failure_returns_502(app):
    publisher = AsyncMock()
    publisher.publish_turn = AsyncMock(side_effect=RuntimeError("nats down"))
    app.state.memory_publisher = publisher

    async with await _http(app) as ac:
        resp = await ac.post(
            "/api/memory/memories",
            json={"user_id": "alice", "text": "hello"},
        )
    assert resp.status_code == 502
    assert "nats down" in resp.json()["detail"]


async def test_kg_add_triple(app):
    with patch(
        "eidolon_admin_server.app.memory.routers.writes.call_tool",
        new=AsyncMock(return_value={"status": "applied", "triple_id": "t-1"}),
    ) as mock:
        async with await _http(app) as ac:
            resp = await ac.post(
                "/api/memory/kg/triples",
                json={
                    "user_id": "alice",
                    "subject": "alice",
                    "predicate": "likes",
                    "object": "tea",
                    "confidence": 0.9,
                },
            )

    assert resp.status_code == 202
    assert resp.json() == {"status": "applied", "request_id": None, "triple_id": "t-1"}
    args = mock.await_args.args
    assert args[1] == "eidolon_memory_kg_add_triple"
    assert "user_id" not in args[2]  # user_id is the routing key, not a tool arg
    assert args[2]["confidence"] == 0.9


async def test_kg_invalidate(app):
    with patch(
        "eidolon_admin_server.app.memory.routers.writes.call_tool",
        new=AsyncMock(return_value={"status": "pending", "request_id": "req-7"}),
    ) as mock:
        async with await _http(app) as ac:
            resp = await ac.post(
                "/api/memory/kg/invalidations",
                json={
                    "user_id": "alice",
                    "subject": "alice",
                    "predicate": "likes",
                    "object": "tea",
                },
            )

    assert resp.status_code == 202
    assert resp.json()["request_id"] == "req-7"
    args = mock.await_args.args
    assert args[1] == "eidolon_memory_kg_invalidate"


# -- errors -------------------------------------------------------------------


async def test_unknown_user_returns_404(app):
    """resolve_user raises MemoryUserNotFound when user_id absent from yaml."""
    # We don't mock call_tool here — it shouldn't even be reached because
    # resolve_user runs first inside open_session.
    async with await _http(app) as ac:
        resp = await ac.get(
            "/api/memory/memories",
            params={"user_id": "ghost", "limit": 10, "offset": 0},
        )
    assert resp.status_code == 404
    assert "ghost" in resp.json()["detail"]
