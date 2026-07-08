from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import AsyncIterator

import httpx
import pytest
from eidolon_data import DataSettings, DataStore
from fastapi import FastAPI

from eidolon_admin_server.app.data import router as data_router
from eidolon_admin_server.app.data.owner_delete_finalizer import (
    OwnerDeleteJournal,
    finalize_owner_delete_jobs,
)
from eidolon_admin_server.app.memory.runners import memory_palace_path


def _runtime_device(
    device_id: str,
    *,
    name: str = "ESP Device",
    status: str = "online",
    approved: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        device_id=device_id,
        name=name,
        kind="esp32",
        enabled=True,
        approved=approved,
        approved_at=datetime.now(timezone.utc) if approved else None,
        last_seen=datetime.now(timezone.utc),
        status=status,
        room_name=f"{device_id}-control",
        missed_probes=0,
    )


class FakeHubDeviceClient:
    def __init__(self) -> None:
        self.devices = {
            "esp-near": _runtime_device("esp-near", name="Nearby ESP", approved=True),
            "esp-pending": _runtime_device("esp-pending", name="Pending ESP"),
            "esp-owned": _runtime_device("esp-owned", name="Owned ESP"),
            "esp-ghost": _runtime_device("esp-ghost", name="Ghost ESP", status="offline", approved=True),
        }
        self.approved: list[str] = []

    async def list_devices(self) -> list[SimpleNamespace]:
        return list(self.devices.values())

    async def get_device(self, device_id: str) -> SimpleNamespace:
        return self.devices[device_id]

    async def identify_device(self, device_id: str) -> dict:
        return {
            "command_id": "cmd-identify",
            "device_id": device_id,
            "op": "device.identify",
            "status": "sent",
        }

    async def refresh_device_config(self, device_id: str) -> None:
        return None


@pytest.fixture
async def data_store(tmp_path) -> AsyncIterator[DataStore]:
    store = DataStore.open(DataSettings(sqlite_path=str(tmp_path / "eidolon.sqlite3")))
    await store.init_schema()
    yield store
    await store.close()


@pytest.fixture
async def client(data_store: DataStore) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.state.data_store = data_store
    app.include_router(data_router, prefix="/api")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


async def test_owner_scoped_data_overview_and_lists(
    client: httpx.AsyncClient,
    data_store: DataStore,
) -> None:
    created = await client.post(
        "/api/owners",
        json={"owner_id": "owner-a", "display_name": "Owner A", "kind": "person"},
    )
    assert created.status_code == 201
    assert created.json()["status"] == "active"

    duplicate = await client.post(
        "/api/owners",
        json={"owner_id": "owner-a", "display_name": "Owner A"},
    )
    assert duplicate.status_code == 409

    empty_overview = await client.get("/api/owners/owner-a/workspace")
    assert empty_overview.status_code == 200
    assert empty_overview.json()["initialized"] is False

    initialized = await client.post(
        "/api/owners/owner-a/workspace/initialize",
        json={
            "companion_display_name": "Xiaoyi",
            "genome_json": {"tone": "warm"},
            "prompt_markdown": "# Xiaoyi\n\n## Style\n\n- Warm.\n",
            "memory_policy_json": {"scope": "owner"},
        },
    )
    assert initialized.status_code == 200
    assert initialized.json()["companion"]["companion_id"] == "c_owner-a_default"
    assert initialized.json()["companion"]["companion_type"] == "master"
    assert initialized.json()["persona_genome"]["genome_id"] == "g_owner-a_default_v1"
    assert initialized.json()["persona_genome"]["status"] == "committed"
    assert initialized.json()["persona_genome"]["prompt_markdown"].startswith("# Xiaoyi")
    assert initialized.json()["memory_realm"]["realm_id"] == "r_owner-a_default"

    await data_store.devices.create_device(
        device_id="device-a",
        owner_id="owner-a",
        name="Desk Body",
        kind="voice_body",
        bound_companion_id="c_owner-a_default",
    )
    await data_store.conversations.create_conversation(
        conversation_id="conversation-a",
        owner_id="owner-a",
        companion_id="c_owner-a_default",
        source_device_id="device-a",
        title="Morning",
    )
    await data_store.jobs.create(
        job_id="job-a",
        owner_id="owner-a",
        companion_id="c_owner-a_default",
        provider="mementos",
        kind="daily_report",
    )

    overview = await client.get("/api/owners/owner-a/workspace")
    assert overview.status_code == 200
    body = overview.json()
    assert body["owner"]["owner_id"] == "owner-a"
    assert body["initialized"] is True
    assert body["counts"] == {
        "companions": 1,
        "persona_genomes": 1,
        # The master companion is provisioned with a host-local web body,
        # so device-a is the owner's *second* device.
        "devices": 2,
        "conversations": 1,
        "memory_realms": 1,
        "jobs": 1,
        # +1 for device.web_body.provisioned.
        "events": 6,
    }
    assert body["companions"][0]["companion_id"] == "c_owner-a_default"
    assert body["companions"][0]["is_master"] is True
    assert body["companions"][0]["companion_type"] == "master"
    device_ids = {device["device_id"] for device in body["devices"]}
    assert device_ids == {"web-c_owner-a_default", "device-a"}
    assert body["conversations"][0]["conversation_id"] == "conversation-a"
    assert body["memory_realms"][0]["realm_id"] == "r_owner-a_default"
    assert body["jobs"][0]["job_id"] == "job-a"
    assert {event["event_type"] for event in body["events"]} >= {"owner.created", "companion.workspace.initialized"}

    genomes = await client.get("/api/owners/owner-a/persona-genomes")
    assert genomes.status_code == 200
    assert genomes.json()["persona_genomes"][0]["genome_id"] == "g_owner-a_default_v1"
    assert genomes.json()["persona_genomes"][0]["prompt_markdown"].startswith("# Xiaoyi")

    missing = await client.get("/api/owners/missing/workspace")
    assert missing.status_code == 404


async def test_companion_web_body_and_multi_body_binding(
    client: httpx.AsyncClient,
    data_store: DataStore,
) -> None:
    await client.post(
        "/api/owners",
        json={"owner_id": "owner-bodies", "display_name": "Owner", "kind": "person"},
    )
    initialized = await client.post(
        "/api/owners/owner-bodies/workspace/initialize",
        json={"companion_display_name": "Xiaoyi"},
    )
    assert initialized.status_code == 200
    companion_id = initialized.json()["companion"]["companion_id"]
    assert initialized.json()["companion"]["is_master"] is True

    # Master already has a web body; the one-click endpoint is idempotent.
    web1 = await client.post(
        f"/api/owners/owner-bodies/companions/{companion_id}/devices/web"
    )
    assert web1.status_code == 200
    assert web1.json()["kind"] == "web"
    assert web1.json()["device_id"] == f"web-{companion_id}"
    web2 = await client.post(
        f"/api/owners/owner-bodies/companions/{companion_id}/devices/web"
    )
    assert web2.status_code == 200
    assert web2.json()["device_id"] == web1.json()["device_id"]

    # Associate a second, physical body to the same companion (previously blocked).
    await data_store.devices.create_device(
        device_id="esp-bodies",
        owner_id="owner-bodies",
        kind="esp32",
        status="active",
    )
    bound = await client.post(
        "/api/owners/owner-bodies/devices/esp-bodies/bind-companion",
        params={"companion_id": companion_id},
    )
    assert bound.status_code == 200
    assert bound.json()["bound_companion_id"] == companion_id

    listing = await client.get(
        f"/api/owners/owner-bodies/companions/{companion_id}/devices"
    )
    assert listing.status_code == 200
    device_ids = {device["device_id"] for device in listing.json()["devices"]}
    assert device_ids == {f"web-{companion_id}", "esp-bodies"}


async def test_delete_owner_requires_confirmation_and_removes_tree(
    client: httpx.AsyncClient,
    data_store: DataStore,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv(
        "EIDOLON_OWNER_DELETE_JOURNAL_DIR",
        str(tmp_path / "owner-delete-journal"),
    )
    monkeypatch.setenv(
        "EIDOLON_OWNER_BACKUP_ROOT",
        str(tmp_path / "backup"),
    )
    monkeypatch.setenv(
        "EIDOLON_MEMORY_PALACES_ROOT",
        str(tmp_path / "mempalaces"),
    )
    created = await client.post(
        "/api/owners",
        json={"owner_id": "owner-delete", "display_name": "Delete Me"},
    )
    assert created.status_code == 201
    initialized = await client.post(
        "/api/owners/owner-delete/workspace/initialize",
        json={"companion_display_name": "Delete Companion"},
    )
    assert initialized.status_code == 200
    companion_id = initialized.json()["companion"]["companion_id"]
    palace = Path(memory_palace_path("r_owner-delete_default"))
    palace.mkdir(parents=True)
    (palace / "memory.txt").write_text("remember this", encoding="utf-8")

    rejected = await client.delete(
        "/api/owners/owner-delete",
        params={"confirm_owner_id": "wrong", "purge_memory": "false"},
    )
    assert rejected.status_code == 412
    assert await data_store.owners.get("owner-delete") is not None

    deleted = await client.delete(
        "/api/owners/owner-delete",
        params={"confirm_owner_id": "owner-delete", "purge_memory": "false"},
    )
    assert deleted.status_code == 200
    body = deleted.json()
    assert body["deleted"] is True
    assert body["owner_id"] == "owner-delete"
    assert body["counts"]["companions"] == 1
    assert body["counts"]["memory_realms"] == 1
    assert body["realm_ids"] == ["r_owner-delete_default"]
    assert body["backup"]["backup_id"].startswith("owner-delete-")
    assert Path(body["backup"]["manifest_path"]).is_file()
    palace_backups = body["backup"]["memory_palaces"]
    assert palace_backups[0]["realm_id"] == "r_owner-delete_default"
    assert Path(palace_backups[0]["target"], "memory.txt").read_text(encoding="utf-8") == "remember this"
    assert [item["key"] for item in body["progress"]] == [
        "confirmed",
        "backup",
        "journal",
        "database",
        "memory",
        "done",
    ]
    assert await data_store.owners.get("owner-delete") is None
    assert await data_store.companions.get(companion_id) is None
    assert await data_store.devices.get_device(f"web-{companion_id}") is None


async def test_owner_delete_finalizer_resumes_after_interruption(
    data_store: DataStore,
    tmp_path,
) -> None:
    await data_store.owner_service.create_owner(
        owner_id="owner-resume",
        display_name="Resume Owner",
    )
    result = await data_store.workspace_provisioning.provision_workspace(
        owner_id="owner-resume",
        companion_id="c_resume",
        genome_id="g_resume",
        realm_id="r_resume",
        is_master=True,
    )
    device = await data_store.workspace_provisioning.ensure_web_body(
        owner_id="owner-resume",
        companion_id=result.companion.companion_id,
    )

    journal = OwnerDeleteJournal(tmp_path / "owner-delete-journal")
    journal.create_or_load(owner_id="owner-resume", realm_ids=["r_resume"])

    cleanup = await finalize_owner_delete_jobs(
        data_store,
        None,
        journal=journal,
    )

    assert cleanup["attempted"] == 1
    assert cleanup["finalized"] == 1
    assert cleanup["pending"] == 0
    assert await data_store.owners.get("owner-resume") is None
    assert await data_store.companions.get("c_resume") is None
    assert await data_store.devices.get_device(device.device_id) is None
    assert journal.pending() == []
    assert list((tmp_path / "owner-delete-journal" / "completed").glob("*.json"))


async def test_owner_nearby_devices_identify_and_add_to_owner(
    data_store: DataStore,
) -> None:
    app = FastAPI()
    app.state.data_store = data_store
    fake_hub = FakeHubDeviceClient()
    app.state.hub_device_client = fake_hub
    app.include_router(data_router, prefix="/api")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        await client.post("/api/owners", json={"owner_id": "owner-devices"})
        await client.post(
            "/api/owners/owner-devices/workspace/initialize",
            json={"companion_display_name": "Companion"},
        )
        await data_store.devices.create_device(
            device_id="esp-owned",
            owner_id="owner-devices",
            name="Owned ESP",
            kind="esp32",
        )

        nearby = await client.get("/api/owners/owner-devices/nearby-devices")
        assert nearby.status_code == 200
        assert nearby.json()["hub_available"] is True
        assert [row["device_id"] for row in nearby.json()["devices"]] == ["esp-near"]
        assert nearby.json()["devices"][0]["approved"] is True

        identify = await client.post("/api/owners/owner-devices/nearby-devices/esp-near/identify")
        assert identify.status_code == 200
        assert identify.json()["op"] == "device.identify"

        pending_identify = await client.post("/api/owners/owner-devices/nearby-devices/esp-pending/identify")
        assert pending_identify.status_code == 412

        pending_claim = await client.post(
            "/api/owners/owner-devices/nearby-devices/esp-pending/claim",
            json={"name": "Pending ESP"},
        )
        assert pending_claim.status_code == 412

        added = await client.post(
            "/api/owners/owner-devices/nearby-devices/esp-near/claim",
            json={
                "name": "Nearby ESP",
                "companion_id": "c_owner-devices_default",
                "interaction_mode": "voice",
            },
        )
        assert added.status_code == 200
        assert added.json()["owner_id"] == "owner-devices"
        assert added.json()["status"] == "active"
        assert added.json()["bound_companion_id"] == "c_owner-devices_default"
        assert added.json()["metadata_json"]["source"] == "hub_runtime"
        assert added.json()["metadata_json"]["hub_approved"] is True
        assert fake_hub.approved == []

        updated = await client.patch(
            "/api/owners/owner-devices/devices/esp-near",
            json={"name": "box-3", "metadata_json": {"aliases": ["box-3", "客厅音箱"]}},
        )
        assert updated.status_code == 200
        assert updated.json()["name"] == "box-3"
        assert updated.json()["metadata_json"]["aliases"] == ["box-3", "客厅音箱"]
        assert updated.json()["metadata_json"]["source"] == "hub_runtime"

        owner_identify = await client.post("/api/owners/owner-devices/devices/esp-near/identify")
        assert owner_identify.status_code == 200
        assert owner_identify.json()["op"] == "device.identify"

        # A companion may hold multiple bodies, so binding a second device to
        # the same companion is allowed (no longer a 409).
        fake_hub.devices["esp-second"] = _runtime_device("esp-second", name="Second ESP", approved=True)
        second_body = await client.post(
            "/api/owners/owner-devices/nearby-devices/esp-second/claim",
            json={
                "name": "Second ESP",
                "companion_id": "c_owner-devices_default",
                "interaction_mode": "voice",
            },
        )
        assert second_body.status_code == 200
        assert second_body.json()["bound_companion_id"] == "c_owner-devices_default"

        # Both nearby devices are now claimed, so nothing remains unclaimed.
        empty_nearby = await client.get("/api/owners/owner-devices/nearby-devices")
        assert empty_nearby.status_code == 200
        assert empty_nearby.json()["devices"] == []

        unbound = await client.post("/api/owners/owner-devices/devices/esp-near/bind-companion")
        assert unbound.status_code == 200
        assert unbound.json()["bound_companion_id"] is None

        released = await client.post("/api/owners/owner-devices/devices/esp-near/release")
        assert released.status_code == 200
        assert released.json()["owner_id"] is None
        assert released.json()["bound_companion_id"] is None

        events = await data_store.events.list_for_subject(
            subject_type="device",
            subject_id="esp-near",
        )
        assert [event.event_type for event in events] == [
            "device.claimed",
            "device.bound_companion",
            "device.updated",
            "device.bound_companion",
            "device.released",
        ]


async def test_data_router_returns_503_without_datastore() -> None:
    app = FastAPI()
    app.state.data_store = None
    app.include_router(data_router, prefix="/api")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        response = await ac.get("/api/owners")
    assert response.status_code == 503


async def test_delete_non_master_companion(client: httpx.AsyncClient) -> None:
    await client.post(
        "/api/owners", json={"owner_id": "od", "display_name": "OD", "kind": "person"}
    )
    # First initialize -> master companion.
    await client.post("/api/owners/od/workspace/initialize", json={})
    # Second initialize with explicit ids -> a non-master companion.
    second = await client.post(
        "/api/owners/od/workspace/initialize",
        json={"companion_id": "c_side", "genome_id": "g_side", "realm_id": "r_side"},
    )
    assert second.status_code == 200
    assert second.json()["companion"]["is_master"] is False

    deleted = await client.request(
        "DELETE", "/api/owners/od/companions/c_side", params={"purge_memory": "false"}
    )
    assert deleted.status_code == 200
    body = deleted.json()
    assert body["deleted"] is True
    assert "r_side" in body["realm_ids"]
    assert body["counts"]["companions"] == 1

    remaining = await client.get("/api/owners/od/companions")
    ids = {c["companion_id"] for c in remaining.json()["companions"]}
    assert "c_side" not in ids
    assert "c_od_default" in ids


async def test_delete_master_companion_is_refused(client: httpx.AsyncClient) -> None:
    await client.post(
        "/api/owners", json={"owner_id": "om", "display_name": "OM", "kind": "person"}
    )
    init = await client.post("/api/owners/om/workspace/initialize", json={})
    master_id = init.json()["companion"]["companion_id"]
    refused = await client.request(
        "DELETE", f"/api/owners/om/companions/{master_id}", params={"purge_memory": "false"}
    )
    assert refused.status_code == 409


async def test_promote_companion_to_master(client: httpx.AsyncClient) -> None:
    await client.post(
        "/api/owners", json={"owner_id": "op", "display_name": "OP", "kind": "person"}
    )
    await client.post("/api/owners/op/workspace/initialize", json={})  # master c_op_default
    await client.post(
        "/api/owners/op/workspace/initialize",
        json={"companion_id": "c_two", "genome_id": "g_two", "realm_id": "r_two"},
    )
    promoted = await client.post("/api/owners/op/companions/c_two/promote-master")
    assert promoted.status_code == 200
    assert promoted.json()["is_master"] is True

    devices = await client.get("/api/owners/op/companions/c_two/devices")
    assert "web" in {d["kind"] for d in devices.json()["devices"]}

    companions = await client.get("/api/owners/op/companions")
    masters = [c["companion_id"] for c in companions.json()["companions"] if c["is_master"]]
    assert masters == ["c_two"]


async def test_bootstrap_uses_existing_master(
    client: httpx.AsyncClient, monkeypatch
) -> None:
    monkeypatch.delenv("EIDOLON_LOCAL_OWNER_ID", raising=False)
    await client.post(
        "/api/owners", json={"owner_id": "solo", "display_name": "Solo", "kind": "person"}
    )
    await client.post("/api/owners/solo/workspace/initialize", json={})
    resp = await client.get("/api/bootstrap")
    assert resp.status_code == 200
    body = resp.json()
    assert body["owner_id"] == "solo"
    assert body["master_source"] == "existing"
    assert body["companion_id"] == "c_solo_default"
    assert body["device_id"] == "web-c_solo_default"


async def test_bootstrap_provisions_master_when_owner_has_none(
    client: httpx.AsyncClient, monkeypatch
) -> None:
    monkeypatch.delenv("EIDOLON_LOCAL_OWNER_ID", raising=False)
    await client.post(
        "/api/owners", json={"owner_id": "fresh", "display_name": "Fresh", "kind": "person"}
    )
    resp = await client.get("/api/bootstrap")
    assert resp.status_code == 200
    body = resp.json()
    assert body["master_source"] == "provisioned"
    assert body["companion_id"] == "c_fresh_default"
    assert body["device_id"] == "web-c_fresh_default"


async def test_bootstrap_promotes_lone_non_master(
    client: httpx.AsyncClient, data_store: DataStore, monkeypatch
) -> None:
    monkeypatch.delenv("EIDOLON_LOCAL_OWNER_ID", raising=False)
    await client.post(
        "/api/owners", json={"owner_id": "lone", "display_name": "Lone", "kind": "person"}
    )
    # Create a single NON-master companion directly (the API's initialize would
    # force the first companion to master).
    await data_store.workspace_provisioning.provision_workspace(
        owner_id="lone", companion_id="c_only", genome_id="g_only", realm_id="r_only", is_master=False
    )
    resp = await client.get("/api/bootstrap")
    assert resp.status_code == 200
    body = resp.json()
    assert body["master_source"] == "promoted"
    assert body["companion_id"] == "c_only"
    assert body["device_id"] == "web-c_only"


async def test_bootstrap_ambiguous_multiple_owners(
    client: httpx.AsyncClient, monkeypatch
) -> None:
    monkeypatch.delenv("EIDOLON_LOCAL_OWNER_ID", raising=False)
    await client.post("/api/owners", json={"owner_id": "a1", "kind": "person"})
    await client.post("/api/owners", json={"owner_id": "a2", "kind": "person"})
    resp = await client.get("/api/bootstrap")
    assert resp.status_code == 409

    # EIDOLON_LOCAL_OWNER_ID disambiguates.
    monkeypatch.setenv("EIDOLON_LOCAL_OWNER_ID", "a2")
    resp2 = await client.get("/api/bootstrap")
    assert resp2.status_code == 200
    assert resp2.json()["owner_id"] == "a2"


async def test_bootstrap_no_owner_is_404(client: httpx.AsyncClient, monkeypatch) -> None:
    monkeypatch.delenv("EIDOLON_LOCAL_OWNER_ID", raising=False)
    resp = await client.get("/api/bootstrap")
    assert resp.status_code == 404


async def test_bootstrap_explicit_owner_id(client: httpx.AsyncClient, monkeypatch) -> None:
    monkeypatch.delenv("EIDOLON_LOCAL_OWNER_ID", raising=False)
    await client.post("/api/owners", json={"owner_id": "p1", "kind": "person"})
    await client.post("/api/owners", json={"owner_id": "p2", "kind": "person"})
    # Ambiguous without a hint...
    assert (await client.get("/api/bootstrap")).status_code == 409
    # ...but explicit owner_id resolves that owner (fallback-picker path).
    resp = await client.get("/api/bootstrap", params={"owner_id": "p2"})
    assert resp.status_code == 200
    assert resp.json()["owner_id"] == "p2"
    assert resp.json()["device_id"] == "web-c_p2_default"
    # Unknown explicit owner -> 404.
    assert (await client.get("/api/bootstrap", params={"owner_id": "nope"})).status_code == 404
