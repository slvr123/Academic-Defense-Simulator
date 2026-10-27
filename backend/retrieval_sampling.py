"""Seeded near-tie sampling for new-topic retrieval (v2.0 M6 H3).

With `top_k=1` a fixed archetype query always takes the same chunk, so every
defense on a document asks from the same pages. M5's eval found that most first
picks win by under 0.02 (the pooled near-tie rate was 62.5%), so the best chunk
is often no better than its neighbours. A new-topic turn now picks at random among
the non-excluded chunks within NEAR_TIE_GAP of the best; anything outside that band
can't be picked, so relevance can't fall by more than the band. The generator is
seeded from (session id, turn number), so a defense replays exactly.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass

import numpy as np

from academic_defense_simulator.rag.embeddings import TextEncoder
from academic_defense_simulator.rag.retrieval import Chunk

# The near-tie threshold M4's Check B used: a gap under this is a near-tie.
NEAR_TIE_GAP = 0.02


def turn_seed(session_id: str, turn: int) -> int:
    """A seed from (session id, 1-based turn number), stable across processes
    (unlike hash(), which Python salts per process)."""
    digest = hashlib.sha256(f"{session_id}:{turn}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def near_tie_band(scores: np.ndarray, excluded: frozenset[int], gap: float = NEAR_TIE_GAP) -> list[int]:
    """Non-excluded chunk indices scoring less than `gap` below the best of them,
    best first (ties in index order). Empty when every chunk is excluded."""
    candidates = [i for i in range(len(scores)) if i not in excluded]
    if not candidates:
        return []
    best = max(float(scores[i]) for i in candidates)
    band = [i for i in candidates if best - float(scores[i]) < gap]
    return sorted(band, key=lambda i: (-float(scores[i]), i))


def pick_near_tie(scores: np.ndarray, excluded: frozenset[int], seed: int, gap: float = NEAR_TIE_GAP) -> int | None:
    """One chunk from the near-tie band, chosen by `seed`; None when nothing is left."""
    band = near_tie_band(scores, excluded, gap)
    return random.Random(seed).choice(band) if band else None


@dataclass(frozen=True)
class NearTieRetriever:
    """`retrieve`'s interface (only top_k=1, which is all the engine asks for), with
    the near-tie pick in place of the argmax. Embeds the query through
    `embedding_model` exactly as `retrieve` does, so RecordingEncoder still sees it."""

    seed: int

    def __call__(
        self,
        query: str,
        chunks: list[Chunk],
        top_k: int = 1,
        *,
        embedding_model: TextEncoder,
        exclude_indices: frozenset[int] = frozenset(),
    ) -> list[tuple[int, Chunk]]:
        if top_k != 1:
            raise ValueError("NearTieRetriever picks one chunk")
        query_vec = np.array(embedding_model.encode([query])[0])
        # Unit vectors, so cosine similarity is a dot product, as in `retrieve`.
        scores = np.array([c.embedding for c in chunks]) @ query_vec
        index = pick_near_tie(scores, exclude_indices, self.seed)
        return [] if index is None else [(index, chunks[index])]
