from __future__ import annotations

import wave
from io import BytesIO
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from eidolon_admin_server.app.registry.voiceprints import VoiceprintStore, router
from eidolon_admin_server.app.registry.voiceprints.verifier import (
    VoiceprintComparison,
    VoiceprintTestReport,
)


class FakeUserOrchestrator:
    async def get_user(self, user_id: str):
        return SimpleNamespace(
            spec=SimpleNamespace(
                tenant_id="default",
                user_id=user_id,
            )
        )


class FakeVoiceprintVerifier:
    def build_profile_embedding(self, *, enrollment_wavs):
        assert enrollment_wavs
        return SimpleNamespace(
            vector=(0.1, 0.2, 0.3),
            dim=3,
            latency_ms=7,
            sample_count=len(enrollment_wavs),
            dtype="float32",
            pooling="mean_l2_normalized",
        )

    def compare(self, *, test_wav, enrollment_wavs, threshold, root):
        assert test_wav.is_file()
        assert len(enrollment_wavs) == 3
        return VoiceprintTestReport(
            threshold=threshold,
            matched=True,
            verdict="pass",
            best_score=0.72,
            average_score=0.66,
            latency_ms=12,
            comparisons=tuple(
                VoiceprintComparison(
                    sample_ref=str(path.relative_to(root)),
                    score=0.6 + index / 20,
                    prediction="yes",
                    latency_ms=4,
                )
                for index, path in enumerate(enrollment_wavs)
            ),
        )


def _wav_bytes(*, sample_rate: int = 16000, frames: int = 1600) -> bytes:
    buf = BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(b"\x00\x00" * frames)
    return buf.getvalue()


@pytest.fixture
async def client(tmp_path):
    app = FastAPI()
    app.state.user_orchestrator = FakeUserOrchestrator()
    app.state.voiceprint_store = VoiceprintStore(tmp_path)
    app.state.voiceprint_verifier = FakeVoiceprintVerifier()
    app.include_router(router, prefix="/api")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c


async def test_voiceprint_enrollment_happy_path(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/users/alice/voiceprint")
    assert r.status_code == 200
    assert r.json()["status"] == "empty"

    r = await client.post("/api/users/alice/voiceprint/enrollments", json={})
    assert r.status_code == 201
    enrollment_id = r.json()["enrollment_id"]

    r = await client.post(
        f"/api/users/alice/voiceprint/enrollments/{enrollment_id}/samples",
        content=_wav_bytes(),
        headers={"content-type": "audio/wav"},
    )
    assert r.status_code == 201
    assert r.json()["duration_ms"] == 100

    r = await client.post(
        f"/api/users/alice/voiceprint/enrollments/{enrollment_id}/complete"
    )
    assert r.status_code == 200
    profile = r.json()["profile"]
    assert profile["user_id"] == "alice"
    assert profile["tenant_id"] == "default"
    assert profile["provider"] == "3d_speaker"
    assert profile["model"] == "campplus_zh_16k_common"
    assert profile["embedding_ref"].endswith("/embeddings/vp_alice_default.json")
    assert profile["duration_ms"] == 100
    assert profile["quality"]["accepted_segments"] == 1
    assert profile["quality"]["embedding"]["status"] == "ready"
    assert profile["quality"]["embedding"]["dim"] == 3

    r = await client.get("/api/users/alice/voiceprint")
    assert r.status_code == 200
    assert r.json()["status"] == "ready"
    assert r.json()["profile"]["profile_id"] == profile["profile_id"]
    assert r.json()["profile"]["embedding_ref"] == profile["embedding_ref"]


async def test_voiceprint_delete_is_idempotent(client: httpx.AsyncClient) -> None:
    r = await client.delete("/api/users/alice/voiceprint")
    assert r.status_code == 204

    r = await client.get("/api/users/alice/voiceprint")
    assert r.status_code == 200
    assert r.json()["status"] == "empty"


async def test_voiceprint_sample_requires_wav_content_type(
    client: httpx.AsyncClient,
) -> None:
    r = await client.post("/api/users/alice/voiceprint/enrollments", json={})
    enrollment_id = r.json()["enrollment_id"]

    r = await client.post(
        f"/api/users/alice/voiceprint/enrollments/{enrollment_id}/samples",
        content=_wav_bytes(),
        headers={"content-type": "application/octet-stream"},
    )

    assert r.status_code == 415


async def test_voiceprint_sample_requires_16k_mono_pcm(
    client: httpx.AsyncClient,
) -> None:
    r = await client.post("/api/users/alice/voiceprint/enrollments", json={})
    enrollment_id = r.json()["enrollment_id"]

    r = await client.post(
        f"/api/users/alice/voiceprint/enrollments/{enrollment_id}/samples",
        content=_wav_bytes(sample_rate=8000),
        headers={"content-type": "audio/wav"},
    )

    assert r.status_code == 400
    assert "sample_rate" in r.json()["detail"]


async def test_cancel_voiceprint_enrollment_removes_pending_samples(
    client: httpx.AsyncClient,
) -> None:
    r = await client.post("/api/users/alice/voiceprint/enrollments", json={})
    enrollment_id = r.json()["enrollment_id"]
    r = await client.post(
        f"/api/users/alice/voiceprint/enrollments/{enrollment_id}/samples",
        content=_wav_bytes(),
        headers={"content-type": "audio/wav"},
    )
    assert r.status_code == 201

    r = await client.delete(
        f"/api/users/alice/voiceprint/enrollments/{enrollment_id}"
    )
    assert r.status_code == 204

    r = await client.post(
        f"/api/users/alice/voiceprint/enrollments/{enrollment_id}/complete"
    )
    assert r.status_code == 404


async def test_voiceprint_test_returns_score_after_enrollment(
    client: httpx.AsyncClient,
) -> None:
    r = await client.post("/api/users/alice/voiceprint/enrollments", json={})
    enrollment_id = r.json()["enrollment_id"]
    for _ in range(3):
        r = await client.post(
            f"/api/users/alice/voiceprint/enrollments/{enrollment_id}/samples",
            content=_wav_bytes(frames=16000),
            headers={"content-type": "audio/wav"},
        )
        assert r.status_code == 201
    r = await client.post(
        f"/api/users/alice/voiceprint/enrollments/{enrollment_id}/complete"
    )
    assert r.status_code == 200
    assert r.json()["profile"]["embedding_ref"]

    r = await client.post(
        "/api/users/alice/voiceprint/test",
        content=_wav_bytes(frames=12000),
        headers={"content-type": "audio/wav"},
    )

    assert r.status_code == 200
    body = r.json()
    assert body["matched"] is True
    assert body["verdict"] == "pass"
    assert body["best_score"] == 0.72
    assert body["threshold"] == 0.31
    assert body["test_audio"]["duration_ms"] == 750
    assert len(body["comparisons"]) == 3


async def test_rebuild_voiceprint_embedding_from_existing_profile(
    client: httpx.AsyncClient,
) -> None:
    r = await client.post("/api/users/alice/voiceprint/enrollments", json={})
    enrollment_id = r.json()["enrollment_id"]
    for _ in range(2):
        r = await client.post(
            f"/api/users/alice/voiceprint/enrollments/{enrollment_id}/samples",
            content=_wav_bytes(frames=16000),
            headers={"content-type": "audio/wav"},
        )
        assert r.status_code == 201

    r = await client.post(
        f"/api/users/alice/voiceprint/enrollments/{enrollment_id}/complete"
    )
    assert r.status_code == 200

    r = await client.post("/api/users/alice/voiceprint/embedding/rebuild")

    assert r.status_code == 200
    profile = r.json()["profile"]
    assert profile["embedding_ref"] == "default/alice/embeddings/vp_alice_default.json"
    assert profile["metadata"]["embedding"]["sample_count"] == 2
