"""Admin API for the companion display-face (digital-human cond_image) asset."""

from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace
from typing import AsyncIterator

import httpx
import pytest
from eidolon_data import DataSettings, DataStore
from eidolon_sdk.biz.persona import build_default_persona_genome, persona_genome_to_json
from fastapi import FastAPI
from PIL import Image

from eidolon_admin_server.app.data import idle_generation
from eidolon_admin_server.app.data.router import router as data_router
from eidolon_admin_server.app.settings import AvatarConfig

# A small stand-in for the fragmented-MP4 idle clip the Ditto service returns.
FAKE_FMP4 = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"idle-clip-body"


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
def avatar_config() -> AvatarConfig:
    # Idle generation disabled by default (empty service_url); tests opt in by
    # setting service_url on this same instance the app reads.
    return AvatarConfig()


@pytest.fixture
async def client(
    data_store: DataStore, avatar_config: AvatarConfig
) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.state.data_store = data_store
    app.state.gateway_config = SimpleNamespace(avatar=avatar_config)
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


async def test_idle_generation_on_upload_when_configured(
    client: httpx.AsyncClient, avatar_config: AvatarConfig, monkeypatch
) -> None:
    avatar_config.service_url = "http://ditto.test"

    async def _fake_generate(**kwargs) -> bytes:
        assert kwargs["service_url"] == "http://ditto.test"
        assert kwargs["image_bytes"]  # the normalized cond image is passed through
        return FAKE_FMP4

    monkeypatch.setattr(idle_generation, "generate_idle_clip", _fake_generate)

    companion_id = await _owner_with_companion(client)
    base = f"/api/owners/owner-a/companions/{companion_id}/face"

    up = await client.post(base, files={"image": ("face.png", _image_bytes(), "image/png")})
    assert up.status_code == 201
    # The upload response is a snapshot before the background task ran → pending.
    assert up.json()["idle_status"] == "pending"

    # ASGITransport awaits background tasks, so by now generation has finished.
    meta = await client.get(base)
    assert meta.status_code == 200
    assert meta.json()["idle_status"] == "ready"
    assert meta.json()["idle_ready"] is True

    video = await client.get(f"{base}/idle/video")
    assert video.status_code == 200
    assert video.headers["content-type"] == "video/mp4"
    assert video.content == FAKE_FMP4


async def test_no_idle_generation_when_unconfigured(client: httpx.AsyncClient) -> None:
    # avatar_config defaults to service_url="" → generation disabled.
    companion_id = await _owner_with_companion(client)
    base = f"/api/owners/owner-a/companions/{companion_id}/face"

    up = await client.post(base, files={"image": ("face.png", _image_bytes(), "image/png")})
    assert up.status_code == 201
    assert up.json()["idle_status"] == "none"
    assert (await client.get(f"{base}/idle/video")).status_code == 404


async def test_idle_generation_failure_is_recorded(
    client: httpx.AsyncClient, avatar_config: AvatarConfig, monkeypatch
) -> None:
    avatar_config.service_url = "http://ditto.test"

    async def _boom(**kwargs) -> bytes:
        raise RuntimeError("ditto unreachable")

    monkeypatch.setattr(idle_generation, "generate_idle_clip", _boom)

    companion_id = await _owner_with_companion(client)
    base = f"/api/owners/owner-a/companions/{companion_id}/face"
    await client.post(base, files={"image": ("face.png", _image_bytes(), "image/png")})

    meta = await client.get(base)
    assert meta.json()["idle_status"] == "failed"
    assert "ditto unreachable" in (meta.json()["idle_error"] or "")
    assert (await client.get(f"{base}/idle/video")).status_code == 404

    # Regenerate recovers once the service is back.
    async def _ok(**kwargs) -> bytes:
        return FAKE_FMP4

    monkeypatch.setattr(idle_generation, "generate_idle_clip", _ok)
    regen = await client.post(f"{base}/idle:regenerate")
    assert regen.status_code == 200
    assert (await client.get(f"{base}/idle/video")).content == FAKE_FMP4


async def test_regenerate_requires_configured_service(client: httpx.AsyncClient) -> None:
    # avatar_config defaults to service_url="" → regenerate is a 409.
    companion_id = await _owner_with_companion(client)
    base = f"/api/owners/owner-a/companions/{companion_id}/face"
    await client.post(base, files={"image": ("face.png", _image_bytes(), "image/png")})
    assert (await client.post(f"{base}/idle:regenerate")).status_code == 409


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
