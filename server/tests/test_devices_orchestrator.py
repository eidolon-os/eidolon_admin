"""Tests for DeviceOrchestrator — the cross-service composition layer.

We use respx to mock hub + agent HTTP endpoints, and a real NATS bucket
(scoped per-test by UUID-suffixed bucket names so parallel runs don't
collide). The orchestrator never sees the difference between mocked-HTTP
and real-HTTP; it just sees an httpx.AsyncClient.

Each test name reads as a contract: "method <does this> when <condition>".
"""
from __future__ import annotations

import uuid

import httpx
import pytest
import respx

from eidolon_admin_server.app.devices.orchestrator import (
    AgentNotFound,
    DeviceNotApproved,
    DeviceNotFound,
    DeviceOrchestrator,
    SoulTooLarge,
    TemplateRenderFailed,
)
from eidolon_admin_server.app.devices.repository import (
    AGENTS_BUCKET,
    MAPPINGS_BUCKET,
    MAX_SOUL_SIZE_BYTES,
    SOULS_BUCKET,
    DeviceBindingRepository,
)
from eidolon_admin_server.app.nats_kv import BucketSpec, KVClient


HUB_URL = "http://hub.test"
AGENT_URL = "http://agent.test"


# ---- fixtures ------------------------------------------------------------


@pytest.fixture
async def kv_client() -> KVClient:
    """Real NATS, but per-test bucket names so state never leaks."""
    client = KVClient()
    try:
        await client.connect()
    except Exception:
        pytest.skip("NATS not reachable at 127.0.0.1:4222")
    yield client
    await client.close()


@pytest.fixture
async def repo(kv_client: KVClient) -> DeviceBindingRepository:
    """Build a repository whose buckets are unique to this test.

    We monkey-patch the module-level BucketSpec constants for the duration
    of the test so the repo (and orchestrator) use the unique names. After
    the test the buckets stay in NATS but are unreachable through any
    business code path — fine for a dev cluster.
    """
    suffix = uuid.uuid4().hex[:10]
    original = (MAPPINGS_BUCKET.name, SOULS_BUCKET.name, AGENTS_BUCKET.name)
    # dataclass is frozen — use object.__setattr__ to swap names in-place.
    object.__setattr__(MAPPINGS_BUCKET, "name", f"test_map_{suffix}")
    object.__setattr__(SOULS_BUCKET, "name", f"test_souls_{suffix}")
    object.__setattr__(AGENTS_BUCKET, "name", f"test_agents_{suffix}")
    r = DeviceBindingRepository(kv_client)
    await r.ensure_buckets()
    yield r
    # restore for any other tests
    object.__setattr__(MAPPINGS_BUCKET, "name", original[0])
    object.__setattr__(SOULS_BUCKET, "name", original[1])
    object.__setattr__(AGENTS_BUCKET, "name", original[2])


@pytest.fixture
async def orchestrator(repo: DeviceBindingRepository) -> DeviceOrchestrator:
    http_client = httpx.AsyncClient(trust_env=False)
    yield DeviceOrchestrator(
        repo=repo,
        http_client=http_client,
        hub_base_url=HUB_URL,
        agent_base_url=AGENT_URL,
    )
    await http_client.aclose()


def _approved_device_payload(device_id: str) -> dict:
    return {
        "devices": [{
            "device_id": device_id,
            "name": "tester",
            "enabled": True,
            "paired": False,
            "approved": True,
            "approved_at": "2026-05-25T00:00:00+00:00",
            "last_seen": None,
            "status": "offline",
        }]
    }


def _unapproved_device_payload(device_id: str) -> dict:
    p = _approved_device_payload(device_id)
    p["devices"][0]["approved"] = False
    p["devices"][0]["approved_at"] = None
    return p


# ---- create_agent: happy path -------------------------------------------


@pytest.mark.asyncio
async def test_create_agent_writes_all_three_buckets_on_success(
    orchestrator: DeviceOrchestrator, repo: DeviceBindingRepository
) -> None:
    with respx.mock:
        respx.get(f"{HUB_URL}/api/admin/devices").mock(
            return_value=httpx.Response(200, json=_approved_device_payload("d1"))
        )
        respx.post(f"{AGENT_URL}/api/admin/personas/templates/caretaker_jiezhi/render").mock(
            return_value=httpx.Response(200, json={
                "markdown": "# soul\n",
                "template_id": "caretaker_jiezhi",
                "template_revision": 1,
            })
        )
        agent_id, preview_chars, is_active = await orchestrator.create_agent(
            device_id="d1", template_id="caretaker_jiezhi", user_id="alice"
        )

    assert is_active is True
    assert preview_chars > 0
    assert agent_id  # non-empty
    # All three buckets must have the new row.
    assert await repo.get_soul(agent_id) == "# soul\n"
    meta = await repo.get_agent_meta(agent_id)
    assert meta is not None and meta.template_id == "caretaker_jiezhi"
    mapping = await repo.get_mapping("d1")
    assert mapping is not None
    assert mapping.agent_ids == [agent_id]
    assert mapping.active_agent_id == agent_id


# ---- create_agent: validation ------------------------------------------


@pytest.mark.asyncio
async def test_create_agent_rejects_unapproved_device(
    orchestrator: DeviceOrchestrator,
) -> None:
    with respx.mock:
        respx.get(f"{HUB_URL}/api/admin/devices").mock(
            return_value=httpx.Response(200, json=_unapproved_device_payload("d1"))
        )
        with pytest.raises(DeviceNotApproved):
            await orchestrator.create_agent(
                device_id="d1", template_id="t", user_id="alice"
            )


@pytest.mark.asyncio
async def test_create_agent_rejects_unknown_device(
    orchestrator: DeviceOrchestrator,
) -> None:
    with respx.mock:
        respx.get(f"{HUB_URL}/api/admin/devices").mock(
            return_value=httpx.Response(200, json={"devices": []})
        )
        with pytest.raises(DeviceNotFound):
            await orchestrator.create_agent(
                device_id="ghost", template_id="t", user_id="alice"
            )


@pytest.mark.asyncio
async def test_create_agent_template_404_surfaces_as_render_failed(
    orchestrator: DeviceOrchestrator,
) -> None:
    with respx.mock:
        respx.get(f"{HUB_URL}/api/admin/devices").mock(
            return_value=httpx.Response(200, json=_approved_device_payload("d1"))
        )
        respx.post(f"{AGENT_URL}/api/admin/personas/templates/missing/render").mock(
            return_value=httpx.Response(404, json={"detail": "not found"})
        )
        with pytest.raises(TemplateRenderFailed):
            await orchestrator.create_agent(
                device_id="d1", template_id="missing", user_id="alice"
            )


@pytest.mark.asyncio
async def test_create_agent_rejects_oversized_render_output(
    orchestrator: DeviceOrchestrator, repo: DeviceBindingRepository
) -> None:
    huge = "x" * (MAX_SOUL_SIZE_BYTES + 10)
    with respx.mock:
        respx.get(f"{HUB_URL}/api/admin/devices").mock(
            return_value=httpx.Response(200, json=_approved_device_payload("d1"))
        )
        respx.post(f"{AGENT_URL}/api/admin/personas/templates/big/render").mock(
            return_value=httpx.Response(200, json={
                "markdown": huge,
                "template_id": "big",
                "template_revision": 1,
            })
        )
        with pytest.raises(SoulTooLarge):
            await orchestrator.create_agent(
                device_id="d1", template_id="big", user_id="alice"
            )
    # And nothing should have been written.
    assert await repo.get_mapping("d1") is None


# ---- switch_active -------------------------------------------------------


@pytest.mark.asyncio
async def test_switch_active_to_unknown_agent_404s(
    orchestrator: DeviceOrchestrator, repo: DeviceBindingRepository
) -> None:
    # Seed a mapping directly so we don't need the create_agent path.
    from eidolon_admin_server.app.devices.repository import Mapping
    await repo.put_mapping("d1", Mapping(user_id="alice", agent_ids=["real"], active_agent_id="real"))
    with pytest.raises(AgentNotFound):
        await orchestrator.switch_active("d1", "imaginary")


# ---- delete_agent --------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_active_agent_falls_back_to_newest_other(
    orchestrator: DeviceOrchestrator, repo: DeviceBindingRepository
) -> None:
    # Bind two agents via the real create_agent flow so created_at is wall-clock ordered.
    with respx.mock:
        respx.get(f"{HUB_URL}/api/admin/devices").mock(
            return_value=httpx.Response(200, json=_approved_device_payload("d1"))
        )
        respx.post(f"{AGENT_URL}/api/admin/personas/templates/A/render").mock(
            return_value=httpx.Response(200, json={"markdown": "a", "template_id": "A", "template_revision": 1})
        )
        respx.post(f"{AGENT_URL}/api/admin/personas/templates/B/render").mock(
            return_value=httpx.Response(200, json={"markdown": "b", "template_id": "B", "template_revision": 1})
        )
        a1, *_ = await orchestrator.create_agent("d1", "A", "alice")
        a2, *_ = await orchestrator.create_agent("d1", "B", "alice")

    # a2 is now active (newer always wins). Delete a2 → fallback to a1.
    new_active, kind = await orchestrator.delete_agent("d1", a2)
    assert new_active == a1
    assert kind == "next_newest"


@pytest.mark.asyncio
async def test_delete_last_agent_clears_active(
    orchestrator: DeviceOrchestrator, repo: DeviceBindingRepository
) -> None:
    with respx.mock:
        respx.get(f"{HUB_URL}/api/admin/devices").mock(
            return_value=httpx.Response(200, json=_approved_device_payload("d1"))
        )
        respx.post(f"{AGENT_URL}/api/admin/personas/templates/A/render").mock(
            return_value=httpx.Response(200, json={"markdown": "a", "template_id": "A", "template_revision": 1})
        )
        a1, *_ = await orchestrator.create_agent("d1", "A", "alice")
    new_active, kind = await orchestrator.delete_agent("d1", a1)
    assert new_active is None
    assert kind == "cleared"


@pytest.mark.asyncio
async def test_delete_non_active_does_not_change_active(
    orchestrator: DeviceOrchestrator, repo: DeviceBindingRepository
) -> None:
    with respx.mock:
        respx.get(f"{HUB_URL}/api/admin/devices").mock(
            return_value=httpx.Response(200, json=_approved_device_payload("d1"))
        )
        respx.post(f"{AGENT_URL}/api/admin/personas/templates/A/render").mock(
            return_value=httpx.Response(200, json={"markdown": "a", "template_id": "A", "template_revision": 1})
        )
        respx.post(f"{AGENT_URL}/api/admin/personas/templates/B/render").mock(
            return_value=httpx.Response(200, json={"markdown": "b", "template_id": "B", "template_revision": 1})
        )
        a1, *_ = await orchestrator.create_agent("d1", "A", "alice")
        a2, *_ = await orchestrator.create_agent("d1", "B", "alice")
        # a2 is active. Delete a1 (non-active) → active stays a2.
        new_active, kind = await orchestrator.delete_agent("d1", a1)
    assert new_active == a2
    assert kind == "no_change"


# ---- update_soul size limit ---------------------------------------------


@pytest.mark.asyncio
async def test_update_soul_rejects_oversized(
    orchestrator: DeviceOrchestrator, repo: DeviceBindingRepository
) -> None:
    with respx.mock:
        respx.get(f"{HUB_URL}/api/admin/devices").mock(
            return_value=httpx.Response(200, json=_approved_device_payload("d1"))
        )
        respx.post(f"{AGENT_URL}/api/admin/personas/templates/A/render").mock(
            return_value=httpx.Response(200, json={"markdown": "ok", "template_id": "A", "template_revision": 1})
        )
        agent_id, *_ = await orchestrator.create_agent("d1", "A", "alice")
    with pytest.raises(SoulTooLarge):
        await orchestrator.update_soul("d1", agent_id, "x" * (MAX_SOUL_SIZE_BYTES + 1))


# ---- create_agent compensation/rollback paths ----------------------------
#
# These are the trickiest correctness properties to enforce: the orchestrator
# writes three NATS buckets in sequence and must reverse earlier writes if a
# later one fails. The pattern: stub a single repo method to raise, drive
# create_agent end-to-end, then assert that no orphan rows remain in NATS.
# Using a real repo + monkey-patching one method keeps the rest of the
# pipeline honest (real serialisation, real NATS interaction); only the
# failure injection is synthetic.


@pytest.mark.asyncio
async def test_create_agent_rolls_back_souls_when_agents_meta_write_fails(
    orchestrator: DeviceOrchestrator,
    repo: DeviceBindingRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If put_agent_meta fails after put_soul succeeded, the soul row must
    be deleted so we don't accumulate orphans in the souls bucket."""

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("simulated NATS failure on agents bucket")

    monkeypatch.setattr(repo, "put_agent_meta", _boom)

    soul_keys_before = await repo._kv.list_keys(SOULS_BUCKET.name, prefix="agent.")  # noqa: SLF001

    with respx.mock:
        respx.get(f"{HUB_URL}/api/admin/devices").mock(
            return_value=httpx.Response(200, json=_approved_device_payload("d1"))
        )
        respx.post(f"{AGENT_URL}/api/admin/personas/templates/A/render").mock(
            return_value=httpx.Response(
                200, json={"markdown": "soul-text", "template_id": "A", "template_revision": 1}
            )
        )
        with pytest.raises(Exception):  # noqa: PT011 — orchestrator wraps as OrchestratorError
            await orchestrator.create_agent("d1", "A", "alice")

    # No new soul key should remain — compensation deleted whatever was written.
    soul_keys_after = await repo._kv.list_keys(SOULS_BUCKET.name, prefix="agent.")  # noqa: SLF001
    assert set(soul_keys_after) == set(soul_keys_before)
    # And no mapping should have been created.
    assert await repo.get_mapping("d1") is None


@pytest.mark.asyncio
async def test_create_agent_rolls_back_souls_and_agents_when_mapping_user_mismatch(
    orchestrator: DeviceOrchestrator,
    repo: DeviceBindingRepository,
) -> None:
    """When an existing mapping points at a different user_id, create_agent
    must refuse — AND clean up the souls + agents rows it just wrote.

    Scenario: alice binds an agent first (sets mapping user=alice). Later
    bob tries to bind on the same device. Orchestrator's defensive check
    rejects bob; the soul and agent-meta it wrote during this attempt must
    not survive.
    """
    # Seed: alice's binding is in place.
    from eidolon_admin_server.app.devices.repository import Mapping
    await repo.put_mapping("d1", Mapping(user_id="alice", agent_ids=["pre-existing"]))

    souls_before = set(await repo._kv.list_keys(SOULS_BUCKET.name, prefix="agent."))  # noqa: SLF001
    agents_before = set(await repo._kv.list_keys(AGENTS_BUCKET.name, prefix="agent."))  # noqa: SLF001

    with respx.mock:
        respx.get(f"{HUB_URL}/api/admin/devices").mock(
            return_value=httpx.Response(200, json=_approved_device_payload("d1"))
        )
        respx.post(f"{AGENT_URL}/api/admin/personas/templates/A/render").mock(
            return_value=httpx.Response(
                200, json={"markdown": "bob's soul", "template_id": "A", "template_revision": 1}
            )
        )
        with pytest.raises(Exception):  # noqa: PT011 — wrapped OrchestratorError
            await orchestrator.create_agent("d1", "A", "bob")

    souls_after = set(await repo._kv.list_keys(SOULS_BUCKET.name, prefix="agent."))  # noqa: SLF001
    agents_after = set(await repo._kv.list_keys(AGENTS_BUCKET.name, prefix="agent."))  # noqa: SLF001
    # No new orphans in either bucket.
    assert souls_after == souls_before, (
        f"expected souls bucket unchanged on rollback; new keys: "
        f"{souls_after - souls_before}"
    )
    assert agents_after == agents_before, (
        f"expected agents bucket unchanged on rollback; new keys: "
        f"{agents_after - agents_before}"
    )
    # And the mapping must still point at alice — bob's failed attempt
    # didn't corrupt the pre-existing binding.
    final = await repo.get_mapping("d1")
    assert final is not None
    assert final.user_id == "alice"
    assert final.agent_ids == ["pre-existing"]
