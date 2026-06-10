"""3D-Speaker-backed voiceprint test comparisons."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class VoiceprintComparison:
    sample_ref: str
    score: float
    prediction: str
    latency_ms: int


@dataclass(frozen=True)
class VoiceprintTestReport:
    threshold: float
    matched: bool
    verdict: str
    best_score: float
    average_score: float
    latency_ms: int
    comparisons: tuple[VoiceprintComparison, ...]


@dataclass(frozen=True)
class VoiceprintEmbeddingResult:
    vector: tuple[float, ...]
    dim: int
    latency_ms: int
    sample_count: int
    pooling: str = "mean_l2_normalized"
    dtype: str = "float32"


class ModelScopeVoiceprintVerifier:
    """Small lazy wrapper around ModelScope's speaker-verification pipeline."""

    def __init__(self, model_dir: str | Path) -> None:
        self.model_dir = Path(model_dir).expanduser()
        self._pipeline: Any | None = None

    def compare(
        self,
        *,
        test_wav: Path,
        enrollment_wavs: list[Path],
        threshold: float,
        root: Path,
    ) -> VoiceprintTestReport:
        if not self.model_dir.is_dir():
            raise RuntimeError(f"3D-Speaker model dir not found: {self.model_dir}")
        if not enrollment_wavs:
            raise RuntimeError("no enrollment WAV samples available")

        pipeline = self._load_pipeline()
        started = time.perf_counter()
        comparisons: list[VoiceprintComparison] = []
        for sample in enrollment_wavs:
            item_started = time.perf_counter()
            result = pipeline([str(sample), str(test_wav)], thr=threshold)
            latency_ms = int((time.perf_counter() - item_started) * 1000)
            score = _score(result)
            comparisons.append(
                VoiceprintComparison(
                    sample_ref=_safe_ref(sample, root=root),
                    score=score,
                    prediction=_prediction(result),
                    latency_ms=latency_ms,
                )
            )

        best_score = max(item.score for item in comparisons)
        average_score = sum(item.score for item in comparisons) / len(comparisons)
        matched = best_score >= threshold
        margin = best_score - threshold
        if matched and margin >= 0.08:
            verdict = "pass"
        elif matched or margin >= -0.05:
            verdict = "uncertain"
        else:
            verdict = "fail"
        return VoiceprintTestReport(
            threshold=threshold,
            matched=matched,
            verdict=verdict,
            best_score=best_score,
            average_score=average_score,
            latency_ms=int((time.perf_counter() - started) * 1000),
            comparisons=tuple(comparisons),
        )

    def build_profile_embedding(
        self,
        *,
        enrollment_wavs: list[Path],
    ) -> VoiceprintEmbeddingResult:
        if not self.model_dir.is_dir():
            raise RuntimeError(f"3D-Speaker model dir not found: {self.model_dir}")
        if not enrollment_wavs:
            raise RuntimeError("no enrollment WAV samples available")

        pipeline = self._load_pipeline()
        started = time.perf_counter()
        result = pipeline([str(path) for path in enrollment_wavs], output_emb=True)
        embeddings = _embeddings(result)
        try:
            import numpy as np
        except ModuleNotFoundError as exc:
            raise RuntimeError("numpy is required to build voiceprint embeddings") from exc

        vectors = np.asarray(embeddings, dtype=np.float32)
        if vectors.ndim != 2 or vectors.shape[0] != len(enrollment_wavs):
            raise RuntimeError(
                f"unexpected embedding shape {vectors.shape}; expected "
                f"({len(enrollment_wavs)}, dim)"
            )
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        vectors = vectors / np.maximum(norms, 1e-12)
        profile = vectors.mean(axis=0)
        profile_norm = np.linalg.norm(profile)
        if profile_norm <= 0:
            raise RuntimeError("voiceprint embedding has zero norm")
        profile = (profile / profile_norm).astype(np.float32)
        return VoiceprintEmbeddingResult(
            vector=tuple(float(value) for value in profile.tolist()),
            dim=int(profile.shape[0]),
            latency_ms=int((time.perf_counter() - started) * 1000),
            sample_count=len(enrollment_wavs),
        )

    def _load_pipeline(self) -> Any:
        if self._pipeline is None:
            try:
                from modelscope.pipelines import pipeline
            except ModuleNotFoundError as exc:
                raise RuntimeError(
                    "modelscope[audio] is not installed in the admin runtime"
                ) from exc
            self._pipeline = pipeline(
                task="speaker-verification",
                model=str(self.model_dir),
            )
        return self._pipeline


def _score(result: Any) -> float:
    if isinstance(result, dict):
        value = result.get("score")
        if isinstance(value, int | float):
            return float(value)
    raise RuntimeError(f"speaker-verification result has no numeric score: {result!r}")


def _embeddings(result: Any) -> Any:
    if isinstance(result, dict) and "embs" in result:
        return result["embs"]
    raise RuntimeError(f"speaker-verification result has no embeddings: {result!r}")


def _prediction(result: Any) -> str:
    if isinstance(result, dict):
        text = str(result.get("text", "")).strip().lower()
        if text in {"yes", "no"}:
            return text
    return "unknown"


def _safe_ref(path: Path, *, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return path.name
