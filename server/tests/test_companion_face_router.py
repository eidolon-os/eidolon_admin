"""Admin API for the companion display-face (digital-human cond_image) asset."""

from __future__ import annotations

from io import BytesIO
from typing import AsyncIterator

import httpx
import pytest
from eidolon_data import DataSettings, DataStore
from eidolon_sdk.biz.persona import build_default_persona_genome, persona_genome_to_json
from fastapi import FastAPI
from PIL import Image

from eidolon_admin_server.app.data.router import router as data_router


@pytest.fixture
async def data_store(tmp_path) -> AsyncIterator[DataStore]:
    store = DataStore.open(
        DataSettings(
            sqlite_path=str(tmp_path / "eidolon.sqlite3"),
            object_store_path=str(tmp_path / "objects"),
        )
    )
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


def _image_bytes(color=(180, 140, 110), size=(512, 512), fmt="PNG") -> bytes:
    output = BytesIO()
    Image.new("RGB", size, color=color).save(output, format=fmt)
    return output.getvalue()


async def _owner_with_companion(client: httpx.AsyncClient) -> str:
    created = await client.post(
        "/api/owners", json={"owner_id": "owner-a", "display_name": "Owner A"}
    )
    assert created.status_code == 201
    initialized = await client.post(
        "/api/owners/owner-a/workspace/initialize",
        json={
            "companion_display_name": "Xiaoyi",
            "genome_json": persona_genome_to_json(build_default_persona_genome(name="Xiaoyi")),
            "memory_policy_json": {"scope": "owner"},
        },
    )
    assert initialized.status_code == 200
    return initialized.json()["companion"]["companion_id"]


async def test_set_get_serve_and_version_face(
    client: httpx.AsyncClient, data_store: DataStore
) -> None:
    companion_id = await _owner_with_companion(client)
    base = f"/api/owners/owner-a/companions/{companion_id}/face"

    # No face configured yet.
    assert (await client.get(base)).status_code == 404
    assert (await client.get(f"{base}/image")).status_code == 404

    # Upload v1 (PNG in → normalized to JPEG).
    r1 = await client.post(base, files={"image": ("face.png", _image_bytes(), "image/png")})
    assert r1.status_code == 201
    body1 = r1.json()
    assert body1["version"] == 1
    assert body1["content_type"] == "image/jpeg"
    assert body1["width"] == 512 and body1["height"] == 512
    assert body1["source"] == "upload"

    # Metadata + served bytes.
    meta = await client.get(base)
    assert meta.status_code == 200 and meta.json()["face_asset_id"] == body1["face_asset_id"]
    img = await client.get(f"{base}/image")
    assert img.status_code == 200
    assert img.headers["content-type"] == "image/jpeg"
    with Image.open(BytesIO(img.content)) as decoded:
        assert decoded.format == "JPEG" and decoded.mode == "RGB"
        assert decoded.getexif() == {}

    # Upload v2 supersedes v1; exactly one active blob remains on disk per version.
    r2 = await client.post(base, files={"image": ("face2.jpg", _image_bytes(color=(20, 60, 200)), "image/jpeg")})
    assert r2.status_code == 201 and r2.json()["version"] == 2
    assert r2.json()["face_asset_id"] != body1["face_asset_id"]
    history = await data_store.companion_face_assets.list_for_companion(companion_id)
    assert [(row.version, row.state) for row in history] == [(2, "active"), (1, "superseded")]


async def test_clear_reverts_to_default(
    client: httpx.AsyncClient, data_store: DataStore
) -> None:
    companion_id = await _owner_with_companion(client)
    base = f"/api/owners/owner-a/companions/{companion_id}/face"
    await client.post(base, files={"image": ("face.png", _image_bytes(), "image/png")})

    cleared = await client.request("DELETE", base)
    assert cleared.status_code == 200 and cleared.json()["cleared"] is True
    assert (await client.get(base)).status_code == 404
    # Clearing again is a no-op.
    assert (await client.request("DELETE", base)).json()["cleared"] is False


async def test_rejects_undersized_and_non_image(
    client: httpx.AsyncClient, data_store: DataStore
) -> None:
    companion_id = await _owner_with_companion(client)
    base = f"/api/owners/owner-a/companions/{companion_id}/face"

    tiny = await client.post(
        base, files={"image": ("tiny.png", _image_bytes(size=(64, 64)), "image/png")}
    )
    assert tiny.status_code == 422

    junk = await client.post(base, files={"image": ("junk.jpg", b"not-an-image", "image/jpeg")})
    assert junk.status_code == 422


async def test_face_requires_owner_and_companion(client: httpx.AsyncClient) -> None:
    # Unknown owner → 404 from _require_owner.
    missing_owner = await client.get(
        "/api/owners/nobody/companions/c-x/face"
    )
    assert missing_owner.status_code == 404

    await _owner_with_companion(client)
    # Owner exists but companion doesn't belong → 400 from _require_owner_companion.
    wrong = await client.get("/api/owners/owner-a/companions/not-mine/face")
    assert wrong.status_code == 400
