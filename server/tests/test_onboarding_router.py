from __future__ import annotations

from typing import AsyncIterator

import httpx
import pytest
from eidolon_data import DataSettings, DataStore
from fastapi import FastAPI

from eidolon_admin_server.app.onboarding import router as onboarding_router


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
    app.state.memory_supervisor_client = None
    app.include_router(onboarding_router, prefix="/api")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


async def test_onboarding_initializes_master_and_launch_identity(
    client: httpx.AsyncClient,
    data_store: DataStore,
) -> None:
    empty = await client.get("/api/onboarding/state")
    assert empty.status_code == 200
    assert empty.json()["missing"] == ["owner"]

    initialized = await client.post(
        "/api/onboarding/initialize",
        json={
            "owner_display_name": "Manson",
            "companion_display_name": "Xiaoyi",
            "character_portrait": "A warm daily companion.",
            "relationship_narrative": "Trusted personal AI companion",
            "voice_portrait": "Calm, concise, gently playful.",
            "pinned_facts": ["Likes morning planning", "Prefers direct summaries"],
        },
    )
    assert initialized.status_code == 200
    state = initialized.json()["state"]
    assert state["master_ready"] is True
    assert state["missing"] == []
    assert state["owner"]["display_name"] == "Manson"
    assert state["master_companion"]["display_name"] == "Xiaoyi"
    assert state["master_companion"]["is_master"] is True
    assert state["master_companion"]["companion_type"] == "master"
    assert state["launch_identity"]["device_id"].startswith("web-")
    assert "owner_id=" in state["launch_identity"]["launch_url"]

    companions = await data_store.companions.list_for_owner(state["owner"]["owner_id"])
    assert len(companions) == 1
    assert companions[0].is_master is True
    assert companions[0].companion_type == "master"
    web_devices = await data_store.devices.list_devices_for_companion(companions[0].companion_id)
    assert len(web_devices) == 1
    assert web_devices[0].device_id == f"web-{companions[0].companion_id}"
    assert web_devices[0].approved_by == "system:onboarding"
    assert web_devices[0].auth_type == "admin_trust"
    assert web_devices[0].access_policy_json["body_commands"] is False
    assert web_devices[0].metadata_json["companion_type"] == "master"

    launched = await client.post(
        "/api/onboarding/launch",
        json={"owner_id": state["owner"]["owner_id"]},
    )
    assert launched.status_code == 200
    assert launched.json()["device_id"] == web_devices[0].device_id

    relaunched = await client.post(
        "/api/onboarding/launch",
        json={"owner_id": state["owner"]["owner_id"]},
    )
    assert relaunched.status_code == 200
    assert relaunched.json()["device_id"] == web_devices[0].device_id
    assert len(await data_store.devices.list_devices_for_companion(companions[0].companion_id)) == 1


async def test_persona_authoring_defaults_and_preview_share_sdk_builder(
    client: httpx.AsyncClient,
) -> None:
    defaults = await client.get(
        "/api/onboarding/persona-authoring/defaults",
        params={"name": "Annie"},
    )
    assert defaults.status_code == 200
    draft = defaults.json()["draft"]
    assert draft["name"] == "Annie"
    assert draft["values"]
    assert draft["character_portrait"]
    assert draft["behavior_guidance"]

    preview = await client.post(
        "/api/onboarding/persona-authoring/preview",
        json={
            "companion_display_name": "Annie",
            "character_portrait": "A candid creative partner.",
            "values": [],
            "boundaries": ["Never fake certainty."],
            "behavior_guidance": ["Name the central tension first."],
        },
    )
    assert preview.status_code == 200
    genome = preview.json()["genome"]
    assert genome["schema_version"] == "eidolon.persona_genome"
    assert genome["constitution"]["values"] == []
    assert genome["constitution"]["boundaries"] == ["Never fake certainty."]
    assert genome["character"]["portrait"] == "A candid creative partner."
    assert genome["expression"]["behavior_guidance"] == [
        "Name the central tension first."
    ]


async def test_onboarding_creates_slave_without_changing_master(
    client: httpx.AsyncClient,
    data_store: DataStore,
) -> None:
    initialized = await client.post(
        "/api/onboarding/initialize",
        json={"owner_display_name": "Owner", "companion_display_name": "Master"},
    )
    owner_id = initialized.json()["state"]["owner"]["owner_id"]
    master_id = initialized.json()["state"]["master_companion"]["companion_id"]

    created = await client.post(
        "/api/onboarding/companions",
        json={
            "owner_id": owner_id,
            "companion_display_name": "Study Buddy",
            "character_portrait": "A focused study companion.",
            "relationship_narrative": "Study partner",
            "voice_portrait": "Sharp and focused.",
            "values": ["clarity", "momentum"],
            "boundaries": ["never invent sources"],
            "behavior_guidance": ["lead with the next action"],
            "pinned_facts": ["Owner is building Eidolon"],
            "safety_boundaries": ["ask before irreversible actions"],
        },
    )
    assert created.status_code == 200
    body = created.json()
    assert body["companion"]["companion_type"] == "slave"
    assert body["companion"]["is_master"] is False
    assert body["launch_identity"] is None
    genome = body["persona_genome"]["genome_json"]
    assert genome["schema_version"] == "eidolon.persona_genome"
    assert genome["constitution"]["values"] == ["clarity", "momentum"]
    assert genome["constitution"]["boundaries"] == ["never invent sources"]
    assert genome["character"]["portrait"] == "A focused study companion."
    assert genome["relationship"]["narrative"] == "Study partner"
    assert genome["relationship"]["pinned_facts"] == ["Owner is building Eidolon"]
    assert genome["relationship"]["safety_boundaries"] == ["ask before irreversible actions"]
    assert genome["expression"]["voice_portrait"] == "Sharp and focused."
    assert genome["expression"]["behavior_guidance"] == ["lead with the next action"]
    assert len(genome["character"]["traits"]) == 9

    state = (await client.get(f"/api/onboarding/state?owner_id={owner_id}")).json()
    assert state["master_companion"]["companion_id"] == master_id
    assert state["master_companion"]["companion_type"] == "master"
    assert {c["companion_type"] for c in state["companions"]} == {"master", "slave"}

    companions = await data_store.companions.list_for_owner(owner_id)
    masters = [c for c in companions if c.is_master or c.companion_type == "master"]
    assert [c.companion_id for c in masters] == [master_id]

    launched = await client.post(
        "/api/onboarding/launch",
        json={"owner_id": owner_id, "companion_id": body["companion"]["companion_id"]},
    )
    assert launched.status_code == 200
    assert launched.json()["device_id"] == f"web-{body['companion']['companion_id']}"
