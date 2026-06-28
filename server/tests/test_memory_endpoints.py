"""Tests for memory read endpoints (Phase 13).

We mock the per-request MCP call_tool / list_tools functions — spinning a real
MCP server in unit tests is overkill, and the per-router logic is mostly
argument shaping + response un-wrapping which is what we actually want to test.
"""
from __future__ import annotations

import json
import sqlite3
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from eidolon_admin_server.app.main import create_app
from eidolon_admin_server.app.memory.routers.realms import _default_memory_realm_id
from eidolon_admin_server.app.memory.runners import RealmEntry
from eidolon_admin_server.app.settings import (
    AdminBindConfig,
    GatewayConfig,
    Settings,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture
def eidolon_data_db(tmp_path):
    db_path = tmp_path / "eidolon.sqlite3"
    conn = sqlite3.connect(db_path)
    with conn:
        conn.execute(
            """
            CREATE TABLE owners (
                owner_id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL DEFAULT '',
                kind TEXT NOT NULL DEFAULT 'person',
                profile_json TEXT NOT NULL DEFAULT '{}',
                settings_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE memory_realms (
                realm_id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                companion_id TEXT NOT NULL,
                engine TEXT NOT NULL DEFAULT 'mempalace',
                engine_config_json TEXT NOT NULL DEFAULT '{}',
                policy_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO owners (
                owner_id, display_name, kind, profile_json, settings_json
            ) VALUES (?, ?, 'person', ?, ?)
            """,
            [
                (
                    "alice",
                    "Alice",
                    json.dumps({"registry": {"enabled": True}}),
                    json.dumps({"memory_port": 8030, "consolidator": {"enabled": True}}),
                ),
                (
                    "bob",
                    "Bob",
                    json.dumps({"registry": {"enabled": False}}),
                    json.dumps({"memory_port": 8031, "consolidator": {"enabled": True}}),
                ),
            ],
        )
        conn.executemany(
            """
            INSERT INTO memory_realms (
                realm_id, owner_id, companion_id, engine, engine_config_json, policy_json, status
            ) VALUES (?, ?, ?, 'mempalace', ?, '{}', ?)
            """,
            [
                ("r:alice:default", "alice", "default", json.dumps({}), "active"),
                ("r:bob:default", "bob", "default", json.dumps({}), "active"),
            ],
        )
    conn.close()
    return db_path


@pytest.fixture
def app(tmp_path, eidolon_data_db, monkeypatch):
    monkeypatch.setenv("EIDOLON_DATA_SQLITE_PATH", str(eidolon_data_db))
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


# -- realms -------------------------------------------------------------------


async def test_realms_list(app):
    with patch(
        "eidolon_admin_server.app.memory.routers.realms.probe_reachable",
        new=AsyncMock(return_value=True),
    ), patch(
        "eidolon_admin_server.app.memory.routers.realms.call_tool",
        new=AsyncMock(return_value={"palace_initialized": True, "ready": True}),
    ):
        async with await _http(app) as ac:
            resp = await ac.get("/api/memory/realms")

    assert resp.status_code == 200
    data = resp.json()
    assert data["realms_source"].endswith("eidolon.sqlite3")
    assert [u["memory_realm_id"] for u in data["realms"]] == ["r:alice:default", "r:bob:default"]
    alice = data["realms"][0]
    assert alice["owner_id"] == "alice"
    assert alice["companion_id"] == "default"
    assert alice["enabled"] is True
    assert alice["agent_reachable"] is True
    assert alice["palace_initialized"] is True
    assert alice["mcp_http_url"].endswith(":8030/mcp")
    # bob is disabled — no probe attempted.
    assert data["realms"][1]["agent_reachable"] is False


async def test_realms_list_default_realm_prefers_first_enabled():
    entries = [
        RealmEntry(memory_realm_id="r:benchmark:default", owner_id="benchmark", companion_id="default", port=8033, enabled=False),
        RealmEntry(memory_realm_id="r:default:default", owner_id="default", companion_id="default", port=8030, enabled=True),
        RealmEntry(memory_realm_id="r:manson:default", owner_id="manson", companion_id="default", port=8031, enabled=True),
    ]
    assert _default_memory_realm_id(entries) == "r:default:default"


class _FakeMemorySupervisorClient:
    async def rebuild_index(self, memory_realm_id: str):
        return {
            "job_id": f"job-{memory_realm_id}",
            "memory_realm_id": memory_realm_id,
            "status": "pending",
            "created_at": "2026-06-20T00:00:00+00:00",
            "started_at": None,
            "finished_at": None,
            "log_path": "/tmp/job.log",
            "error": None,
            "result": None,
        }

    async def get_rebuild_index_job(self, job_id: str):
        return {
            "job_id": job_id,
            "memory_realm_id": "r:alice:default",
            "status": "succeeded",
            "created_at": "2026-06-20T00:00:00+00:00",
            "started_at": "2026-06-20T00:00:01+00:00",
            "finished_at": "2026-06-20T00:00:02+00:00",
            "log_path": "/tmp/job.log",
            "error": None,
            "result": {"returncode": 0},
        }

    async def list_rebuild_index_jobs(self, memory_realm_id: str):
        return {"jobs": [await self.get_rebuild_index_job(f"job-{memory_realm_id}")]}


async def test_rebuild_index_routes_proxy_to_memory_supervisor(app):
    app.state.memory_supervisor_client = _FakeMemorySupervisorClient()
    async with await _http(app) as ac:
        created = await ac.post("/api/memory/realms/r%3Aalice%3Adefault/rebuild-index")
        status = await ac.get("/api/memory/rebuild-index/job-r%3Aalice%3Adefault")
        listed = await ac.get("/api/memory/realms/r%3Aalice%3Adefault/rebuild-index")

    assert created.status_code == 202
    # admin addresses memory-supervisor by memory_realm_id/memory_space_id.
    assert created.json()["job_id"] == "job-r:alice:default"
    assert status.status_code == 200
    assert status.json()["status"] == "succeeded"
    assert listed.status_code == 200
    assert listed.json()["jobs"][0]["memory_realm_id"] == "r:alice:default"


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
                params={"memory_realm_id": "r:alice:default", "query": "hi", "top_k": 3},
            )

    assert resp.status_code == 200
    assert resp.json()["records"] == fake["records"]
    args = mock.await_args.args
    assert args[0] == "r:alice:default"
    assert args[1] == "eidolon_memory_search"
    assert args[2]["query"] == "hi"
    assert args[2]["top_k"] == 3
    assert args[2]["context"]["memory_space_id"] == "r:alice:default"


async def test_memories_list_returns_total_hint(app):
    fake = {"records": [{"key": "k1"}, {"key": "k2"}], "total_hint": 42}
    with patch(
        "eidolon_admin_server.app.memory.routers.memories.call_tool",
        new=AsyncMock(return_value=fake),
    ):
        async with await _http(app) as ac:
            resp = await ac.get("/api/memory/memories", params={"memory_realm_id": "r:alice:default"})

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
            resp = await ac.get("/api/memory/memories", params={"memory_realm_id": "r:alice:default"})

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
            resp = await ac.get("/api/memory/hierarchy", params={"memory_realm_id": "r:alice:default"})

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
                params={"memory_realm_id": "r:alice:default", "max_triples": 50},
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
            resp = await ac.get("/api/memory/graph/palace", params={"memory_realm_id": "r:alice:default"})

    assert resp.json()["capped"] is True


# -- kg -----------------------------------------------------------------------


async def test_kg_predicates(app):
    fake = {"predicates": ["likes", "hates"], "sensitive": ["health_status"]}
    with patch(
        "eidolon_admin_server.app.memory.routers.kg.call_tool",
        new=AsyncMock(return_value=fake),
    ):
        async with await _http(app) as ac:
            resp = await ac.get("/api/memory/kg/predicates", params={"memory_realm_id": "r:alice:default"})

    assert resp.json() == fake


async def test_kg_stats(app):
    fake = {"entities": 12, "triples_total": 50, "triples_active": 40, "triples_invalidated": 10}
    with patch(
        "eidolon_admin_server.app.memory.routers.kg.call_tool",
        new=AsyncMock(return_value=fake),
    ):
        async with await _http(app) as ac:
            resp = await ac.get("/api/memory/kg/stats", params={"memory_realm_id": "r:alice:default"})

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
                params={"memory_realm_id": "r:alice:default", "direction": "both"},
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
                params={"memory_realm_id": "r:alice:default", "entity_name": "alice", "limit": 50},
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
                params={"memory_realm_id": "r:alice:default"},
                json={"query": "what does alice like?", "top_k": 3},
            )

    data = resp.json()
    assert data["context"] == fake["context"]
    assert len(data["kg_triples"]) == 1
    args = mock.await_args.args
    assert args[0] == "r:alice:default"
    assert args[1] == "eidolon_memory_recall_context"
    assert args[2]["query"] == "what does alice like?"
    assert args[2]["context"]["memory_space_id"] == "r:alice:default"


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
            resp = await ac.get("/api/memory/mcp/tools", params={"memory_realm_id": "r:alice:default"})

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
                "memory_realm_id": "r:alice:default",
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
    ctx = kwargs["context"]
    assert ctx.owner_id == "alice"
    assert ctx.companion_id == "default"
    assert ctx.memory_realm_id == "r:alice:default"
    assert ctx.memory_space_id == "r:alice:default"
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
            json={"memory_realm_id": "r:alice:default", "text": "hello"},
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
                    "memory_realm_id": "r:alice:default",
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
    assert "memory_realm_id" not in args[2]  # memory_realm_id is the routing key, not a tool arg
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
                    "memory_realm_id": "r:alice:default",
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


async def test_unknown_realm_returns_404(app):
    """resolve_realm raises MemoryRealmNotFound before opening MCP."""
    # We don't mock call_tool here — it shouldn't even be reached because
    # resolve_realm runs first inside open_session.
    async with await _http(app) as ac:
        resp = await ac.get(
            "/api/memory/memories",
            params={"memory_realm_id": "ghost", "limit": 10, "offset": 0},
        )
    assert resp.status_code == 404
    assert "ghost" in resp.json()["detail"]
