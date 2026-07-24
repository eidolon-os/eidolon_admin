"""Offline generation of a companion's looping idle clip (plan §8.2).

At face-upload time we feed the digital-human (Ditto) service the configured
face plus a few seconds of silence and store the returned fragmented-MP4 as the
companion's idle loop.  This runs off-request (FastAPI ``BackgroundTasks``) so a
slow generation never blocks the upload, and it is fully gated: with no service
URL configured, nothing is generated and the face still works (web falls back to
the still micro-motion).

Generation is deliberately isolated behind :func:`generate_idle_clip` so it can
be stubbed in tests — the real service is remote and self-signed.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import random
import wave
from array import array

import httpx

logger = logging.getLogger(__name__)


def build_idle_wav(
    seconds: float, *, drive_level: float = 0.0, sample_rate: int = 16000
) -> bytes:
    """A mono int16 WAV that drives the idle clip.

    ``drive_level`` 0.0 → pure silence (closed-mouth, minimal motion). Above 0 it
    fills low-level (deterministic) noise scaled to that amplitude, giving Ditto
    more to animate — see ``AvatarConfig.idle_drive_level`` for the caveat that
    higher values read as speech (mouth movement), not richer natural idle.
    """
    frames = max(1, int(seconds * sample_rate))
    if drive_level <= 0:
        pcm = b"\x00\x00" * frames
    else:
        amp = max(1, min(32767, int(drive_level * 32767)))
        rnd = random.Random(0)  # deterministic so regenerating is reproducible
        pcm = array("h", (rnd.randint(-amp, amp) for _ in range(frames))).tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)  # int16
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


async def generate_idle_clip(
    *,
    service_url: str,
    image_bytes: bytes,
    seconds: float,
    width: int,
    height: int,
    fps: float,
    verify_ssl: bool,
    timeout_sec: float,
    drive_level: float = 0.0,
) -> bytes:
    """POST face + silence to Ditto ``/api/stream_video`` and return the fMP4 body.

    Mirrors the channel avatar worker's request shape (multipart ``audio`` /
    ``image`` / ``device_info`` / ``format``) but collects the whole response
    into a single clip instead of streaming it.
    """
    device_info = json.dumps(
        {
            "prefer_fps": fps,
            "format": "mp4",
            "jpeg_quality": 80,
            "max_video_resolution": f"{width}x{height}",
            "screen_width": width,
            "screen_height": height,
        },
        separators=(",", ":"),
    )
    files = {
        "audio": ("idle.wav", build_idle_wav(seconds, drive_level=drive_level), "audio/wav"),
        "image": ("face.jpg", image_bytes, "image/jpeg"),
    }
    data = {"device_info": device_info, "format": "mp4"}
    url = f"{service_url.rstrip('/')}/api/stream_video"
    async with httpx.AsyncClient(verify=verify_ssl, timeout=timeout_sec) as client:
        resp = await client.post(url, files=files, data=data)
        resp.raise_for_status()
        clip = resp.content
    if not clip:
        raise RuntimeError("digital-human service returned an empty idle clip")
    return clip


async def run_idle_generation(store, avatar_cfg, *, face_asset_id: str) -> None:
    """Background task: generate + store the idle clip for one face asset.

    ``avatar_cfg`` is the gateway's :class:`AvatarConfig` (from services.yaml).
    Best-effort and self-contained: marks ``generating`` → ``ready``/``failed``
    on the face asset. Any error is captured onto the row (``idle_error``) rather
    than raised, since it runs detached from the request.
    """
    try:
        asset = await store.companion_face_assets.get(face_asset_id)
        if asset is None:
            logger.warning("[idle-gen] face asset %s vanished before generation", face_asset_id)
            return
        await store.companion_face_assets.set_idle_status(face_asset_id, "generating")
        image_bytes = store.object_storage.get(asset.cond_storage_key)
        clip = await generate_idle_clip(
            service_url=avatar_cfg.service_url,
            image_bytes=image_bytes,
            seconds=avatar_cfg.idle_seconds,
            width=avatar_cfg.width,
            height=avatar_cfg.height,
            fps=avatar_cfg.fps,
            verify_ssl=avatar_cfg.verify_ssl,
            timeout_sec=avatar_cfg.request_timeout_sec,
            drive_level=getattr(avatar_cfg, "idle_drive_level", 0.0),
        )
        digest = hashlib.sha256(clip).hexdigest()
        storage_key = f"{asset.owner_id}/companion-avatar/{asset.companion_id}/idle-{face_asset_id}.mp4"
        store.object_storage.put(storage_key, clip, expected_sha256=digest)
        try:
            await store.companion_face_assets.set_idle_clip(
                face_asset_id,
                storage_key=storage_key,
                content_type="video/mp4",
                size_bytes=len(clip),
                sha256=digest,
            )
        except Exception:
            store.object_storage.delete(storage_key)
            raise
        logger.info(
            "[idle-gen] idle clip ready face_asset=%s bytes=%d", face_asset_id, len(clip)
        )
    except Exception as exc:  # noqa: BLE001 — detached task; record, don't crash
        logger.exception("[idle-gen] idle generation failed for %s", face_asset_id)
        try:
            await store.companion_face_assets.set_idle_status(
                face_asset_id, "failed", error=str(exc)[:500]
            )
        except Exception:
            logger.exception("[idle-gen] could not record idle failure for %s", face_asset_id)
