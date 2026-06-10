"""Filesystem store for voiceprint enrollment samples and profile metadata."""

from __future__ import annotations

import json
import re
import shutil
import uuid
import wave
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from ..schemas.voiceprint import (
    VoiceprintEnrollmentResponse,
    VoiceprintProfileView,
    VoiceprintSampleResponse,
)

_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _validate_id(value: str, *, label: str, max_len: int = 64) -> str:
    if not (1 <= len(value) <= max_len) or not _ID_RE.match(value):
        raise HTTPException(
            status_code=400,
            detail=f"{label} must be 1-{max_len} chars of [A-Za-z0-9_-]",
        )
    return value


def _profile_id(user_id: str) -> str:
    return f"vp_{_validate_id(user_id, label='user_id')}_default"


@dataclass(frozen=True)
class VoiceprintProfile:
    profile_id: str
    tenant_id: str
    user_id: str
    provider: str
    model: str
    embedding_ref: str = ""
    sample_refs: tuple[str, ...] = ()
    sample_rate: int = 16000
    duration_ms: int = 0
    threshold: float | None = None
    quality: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: _utc_now().isoformat())
    updated_at: str = field(default_factory=lambda: _utc_now().isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_view(self) -> VoiceprintProfileView:
        return VoiceprintProfileView.model_validate(
            {**asdict(self), "sample_refs": list(self.sample_refs)}
        )

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["sample_refs"] = list(self.sample_refs)
        return data

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "VoiceprintProfile":
        payload = dict(data)
        payload.setdefault("embedding_ref", "")
        payload["sample_refs"] = tuple(payload.get("sample_refs") or ())
        return cls(**payload)


@dataclass(frozen=True)
class VoiceprintEmbedding:
    profile_id: str
    tenant_id: str
    user_id: str
    provider: str
    model: str
    vector: tuple[float, ...]
    dim: int
    dtype: str = "float32"
    pooling: str = "mean_l2_normalized"
    sample_refs: tuple[str, ...] = ()
    sample_rate: int = 16000
    created_at: str = field(default_factory=lambda: _utc_now().isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["vector"] = list(self.vector)
        data["sample_refs"] = list(self.sample_refs)
        return data


@dataclass(frozen=True)
class Enrollment:
    enrollment_id: str
    tenant_id: str
    user_id: str
    provider: str
    model: str
    sample_rate: int
    created_at: str = field(default_factory=lambda: _utc_now().isoformat())

    def to_response(self, *, sample_count: int) -> VoiceprintEnrollmentResponse:
        return VoiceprintEnrollmentResponse(
            enrollment_id=self.enrollment_id,
            user_id=self.user_id,
            tenant_id=self.tenant_id,
            provider=self.provider,
            model=self.model,
            sample_rate=self.sample_rate,
            sample_count=sample_count,
            created_at=datetime.fromisoformat(self.created_at),
        )


class VoiceprintStore:
    """Persist enrollment WAV samples and profile metadata under one root."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser()

    def get_profile(self, *, tenant_id: str, user_id: str) -> VoiceprintProfile | None:
        path = self._profile_path(tenant_id=tenant_id, user_id=user_id)
        if not path.exists():
            return None
        return VoiceprintProfile.from_json(json.loads(path.read_text(encoding="utf-8")))

    def sample_paths_for_profile(self, profile: VoiceprintProfile) -> list[Path]:
        root = self.root.resolve()
        paths: list[Path] = []
        for ref in profile.sample_refs:
            path = (self.root / ref).expanduser().resolve()
            try:
                path.relative_to(root)
            except ValueError as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"sample_ref escapes voiceprint root: {ref}",
                ) from exc
            if path.is_file():
                paths.append(path)
        return paths

    def save_embedding(
        self,
        *,
        profile: VoiceprintProfile,
        embedding: VoiceprintEmbedding,
    ) -> VoiceprintProfile:
        ref = str(self._embedding_path(profile).relative_to(self.root))
        path = self.root / ref
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(embedding.to_json(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        updated_quality = dict(profile.quality)
        updated_quality["embedding"] = {
            "status": "ready",
            "dim": embedding.dim,
            "dtype": embedding.dtype,
            "pooling": embedding.pooling,
        }
        updated_metadata = dict(profile.metadata)
        updated_metadata["status"] = "ready"
        updated_metadata["embedding"] = {
            "ref": ref,
            "generated_at": embedding.created_at,
            **embedding.metadata,
        }
        updated = VoiceprintProfile(
            **{
                **profile.to_json(),
                "provider": embedding.provider,
                "model": embedding.model,
                "embedding_ref": ref,
                "quality": updated_quality,
                "updated_at": _utc_now().isoformat(),
                "metadata": updated_metadata,
            }
        )
        self._write_profile(updated)
        return updated

    def delete_profile(self, *, tenant_id: str, user_id: str) -> bool:
        path = self._user_dir(tenant_id=tenant_id, user_id=user_id)
        if not path.exists():
            return False
        shutil.rmtree(path)
        return True

    def create_enrollment(
        self,
        *,
        tenant_id: str,
        user_id: str,
        provider: str,
        model: str,
        sample_rate: int,
    ) -> Enrollment:
        tenant_id = _validate_id(tenant_id, label="tenant_id")
        user_id = _validate_id(user_id, label="user_id")
        enrollment = Enrollment(
            enrollment_id=f"vpe_{uuid.uuid4().hex[:12]}",
            tenant_id=tenant_id,
            user_id=user_id,
            provider=_validate_id(provider, label="provider", max_len=64),
            model=_validate_id(model, label="model", max_len=64),
            sample_rate=sample_rate,
        )
        path = self._enrollment_dir(enrollment) / "enrollment.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(enrollment), indent=2) + "\n", encoding="utf-8")
        return enrollment

    def add_sample(
        self,
        *,
        enrollment_id: str,
        tenant_id: str,
        user_id: str,
        wav_bytes: bytes,
    ) -> VoiceprintSampleResponse:
        enrollment = self.load_enrollment(
            enrollment_id=enrollment_id,
            tenant_id=tenant_id,
            user_id=user_id,
        )
        info = _inspect_wav(wav_bytes)
        if info["sample_rate"] != enrollment.sample_rate:
            raise HTTPException(status_code=400, detail="sample_rate mismatch")
        if info["channels"] != 1:
            raise HTTPException(status_code=400, detail="expected mono WAV")
        if info["sample_width"] != 2:
            raise HTTPException(status_code=400, detail="expected 16-bit PCM WAV")

        samples_dir = self._enrollment_dir(enrollment) / "samples"
        samples_dir.mkdir(parents=True, exist_ok=True)
        sample_id = f"sample_{len(list(samples_dir.glob('*.wav'))) + 1:03d}"
        path = samples_dir / f"{sample_id}.wav"
        path.write_bytes(wav_bytes)
        return VoiceprintSampleResponse(
            enrollment_id=enrollment.enrollment_id,
            sample_id=sample_id,
            bytes=len(wav_bytes),
            duration_ms=int(info["duration_ms"]),
            sample_rate=int(info["sample_rate"]),
            channels=int(info["channels"]),
        )

    def complete_enrollment(
        self,
        *,
        enrollment_id: str,
        tenant_id: str,
        user_id: str,
    ) -> VoiceprintProfile:
        enrollment = self.load_enrollment(
            enrollment_id=enrollment_id,
            tenant_id=tenant_id,
            user_id=user_id,
        )
        samples = sorted((self._enrollment_dir(enrollment) / "samples").glob("*.wav"))
        if not samples:
            raise HTTPException(status_code=400, detail="enrollment has no samples")
        total_ms = 0
        for sample in samples:
            total_ms += int(_inspect_wav(sample.read_bytes())["duration_ms"])
        profile = VoiceprintProfile(
            profile_id=_profile_id(user_id),
            tenant_id=tenant_id,
            user_id=user_id,
            provider=enrollment.provider,
            model=enrollment.model,
            sample_refs=tuple(str(p.relative_to(self.root)) for p in samples),
            sample_rate=enrollment.sample_rate,
            duration_ms=total_ms,
            quality={
                "accepted_segments": len(samples),
                "rejected_segments": 0,
            },
            metadata={
                "enrollment_id": enrollment.enrollment_id,
                "status": "placeholder" if enrollment.provider == "noop" else "ready",
            },
        )
        self._write_profile(profile)
        return profile

    def delete_enrollment(
        self,
        *,
        enrollment_id: str,
        tenant_id: str,
        user_id: str,
    ) -> bool:
        enrollment = self.load_enrollment(
            enrollment_id=enrollment_id,
            tenant_id=tenant_id,
            user_id=user_id,
        )
        path = self._enrollment_dir(enrollment)
        if not path.exists():
            return False
        shutil.rmtree(path)
        return True

    def load_enrollment(
        self,
        *,
        enrollment_id: str,
        tenant_id: str,
        user_id: str,
    ) -> Enrollment:
        tenant_id = _validate_id(tenant_id, label="tenant_id")
        user_id = _validate_id(user_id, label="user_id")
        enrollment_id = _validate_id(enrollment_id, label="enrollment_id", max_len=64)
        path = self.root / tenant_id / user_id / "enrollments" / enrollment_id / "enrollment.json"
        if not path.exists():
            raise HTTPException(status_code=404, detail="voiceprint enrollment not found")
        return Enrollment(**json.loads(path.read_text(encoding="utf-8")))

    def sample_count(self, enrollment: Enrollment) -> int:
        samples_dir = self._enrollment_dir(enrollment) / "samples"
        return len(list(samples_dir.glob("*.wav"))) if samples_dir.exists() else 0

    def _profile_path(self, *, tenant_id: str, user_id: str) -> Path:
        return self._user_dir(tenant_id=tenant_id, user_id=user_id) / "profile.json"

    def _embedding_path(self, profile: VoiceprintProfile) -> Path:
        return (
            self._user_dir(tenant_id=profile.tenant_id, user_id=profile.user_id)
            / "embeddings"
            / f"{profile.profile_id}.json"
        )

    def _write_profile(self, profile: VoiceprintProfile) -> None:
        path = self._profile_path(tenant_id=profile.tenant_id, user_id=profile.user_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(profile.to_json(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _user_dir(self, *, tenant_id: str, user_id: str) -> Path:
        tenant_id = _validate_id(tenant_id, label="tenant_id")
        user_id = _validate_id(user_id, label="user_id")
        return self.root / tenant_id / user_id

    def _enrollment_dir(self, enrollment: Enrollment) -> Path:
        return (
            self.root
            / enrollment.tenant_id
            / enrollment.user_id
            / "enrollments"
            / enrollment.enrollment_id
        )


def _inspect_wav(wav_bytes: bytes) -> dict[str, int]:
    try:
        with wave.open(BytesIO(wav_bytes), "rb") as wav:
            channels = wav.getnchannels()
            sample_rate = wav.getframerate()
            sample_width = wav.getsampwidth()
            frames = wav.getnframes()
    except wave.Error as exc:
        raise HTTPException(status_code=400, detail=f"invalid WAV: {exc}") from exc
    duration_ms = int(frames * 1000 / sample_rate) if sample_rate else 0
    return {
        "channels": channels,
        "sample_rate": sample_rate,
        "sample_width": sample_width,
        "frames": frames,
        "duration_ms": duration_ms,
    }


def inspect_wav_bytes(wav_bytes: bytes) -> dict[str, int]:
    """Return WAV metadata and expose validation to router helpers/tests."""
    return _inspect_wav(wav_bytes)
