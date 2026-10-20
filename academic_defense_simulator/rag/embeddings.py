"""Sentence-transformers embedding wrapper."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class TextEncoder(Protocol):
    """What retrieval needs from an embedding model: one unit-length vector per text.
    EmbeddingModel satisfies it; so does the v2.0 backend's ONNX encoder."""

    def encode(self, texts: list[str]) -> list[list[float]]: ...


@dataclass
class EmbeddingModel:
    model_name: str = "sentence-transformers/all-MiniLM-L6-v2"

    def __post_init__(self) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover - optional dependency.
            raise RuntimeError("sentence-transformers is required for embeddings") from exc

        self._model = SentenceTransformer(self.model_name)

    def encode(self, texts: list[str]) -> list[list[float]]:
        vectors = self._model.encode(texts, normalize_embeddings=True)
        return vectors.tolist()
