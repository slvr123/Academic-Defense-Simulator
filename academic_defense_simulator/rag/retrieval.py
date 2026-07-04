"""Chunk retrieval helpers."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from academic_defense_simulator.rag.embeddings import EmbeddingModel


@dataclass(frozen=True)
class Chunk:
    text: str
    embedding: list[float]


def retrieve(
    query: str,
    chunks: list[Chunk],
    top_k: int = 1,
    *,
    embedding_model: EmbeddingModel | None = None,
    exclude_indices: frozenset[int] = frozenset(),
) -> list[tuple[int, Chunk]]:
    """Embed query, cosine-match against chunk embeddings (skipping any index
    in exclude_indices), return top_k as (index, chunk) pairs.

    Has zero knowledge of archetypes, panelists, or personas — the caller
    decides what `query` is.
    """
    candidates = [(i, c) for i, c in enumerate(chunks) if i not in exclude_indices]
    if not candidates:
        return []

    model = embedding_model if embedding_model is not None else EmbeddingModel()
    query_vec = np.array(model.encode([query])[0])
    chunk_matrix = np.array([c.embedding for _, c in candidates])

    # Embeddings are unit-normalized, so cosine similarity == dot product.
    scores = chunk_matrix @ query_vec
    top_order = np.argsort(scores)[::-1][:top_k]
    return [candidates[i] for i in top_order]
