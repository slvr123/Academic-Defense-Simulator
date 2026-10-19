"""Top-k chunk search over a session's embedding matrix. Not an endpoint in M1."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from backend.embedder import Embedder
from backend.sessions import ChunkMeta, Session, SessionStatus


@dataclass(frozen=True)
class SearchHit:
    chunk: ChunkMeta
    score: float  # cosine similarity; rows and query are unit length


def search(session: Session, query: str, k: int, *, embedder: Embedder) -> list[SearchHit]:
    """The k chunks most similar to `query`, best first: matrix @ normalised query."""
    if session.status is not SessionStatus.READY or session.matrix is None:
        raise ValueError(f"session {session.id} is {session.status.value}, not ready")
    if k < 1:
        raise ValueError("k must be at least 1")

    query_vector = embedder.encode_one(query)
    query_vector = query_vector / np.linalg.norm(query_vector)
    scores = session.matrix @ query_vector
    top = np.argsort(scores)[::-1][:k]
    return [SearchHit(chunk=session.chunks[i], score=float(scores[i])) for i in top]
