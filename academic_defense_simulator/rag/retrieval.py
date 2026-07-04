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
) -> list[Chunk]:
    """Embed query, cosine-match against chunk embeddings, return top_k.

    Has zero knowledge of archetypes, panelists, or personas — the caller
    decides what `query` is.
    """
    if not chunks:
        return []

    model = embedding_model if embedding_model is not None else EmbeddingModel()
    query_vec = np.array(model.encode([query])[0])
    chunk_matrix = np.array([c.embedding for c in chunks])

    # Embeddings are unit-normalized, so cosine similarity == dot product.
    scores = chunk_matrix @ query_vec
    top_indices = np.argsort(scores)[::-1][:top_k]
    return [chunks[i] for i in top_indices]
