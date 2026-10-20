"""M1's per-session matrix and ONNX embedder, plugged into v1's engine through its
existing parameters, plus the sources[] each question returns.

v1's `_generate_question` takes `chunks: list[Chunk]` and an `embedding_model` it
only ever calls `.encode(texts)` on, and hands both to `rag.retrieval.retrieve`.
So the engine runs unchanged: `engine_chunks` builds its chunk list from the
session matrix, and `RecordingEncoder` answers `.encode` with the ONNX embedder.

`retrieve` returns the chunk but not its score. `RecordingEncoder` keeps the query
vector it produced, so `source_for` can compute the same cosine `retrieve` ranked
by, with no second embedding. When the engine didn't retrieve (a follow-up stays on
the previous chunk; Devil's Advocate contests an earlier one), the source carries
the score from that chunk's first retrieval.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from academic_defense_simulator.models.session import ConversationTurn
from academic_defense_simulator.panel import DEVILS_ADVOCATE_KEY
from academic_defense_simulator.rag.retrieval import Chunk
from backend.embedder import Embedder
from backend.schemas import Source
from backend.sessions import ChunkMeta


class RecordingEncoder:
    """v1's `EmbeddingModel.encode` interface on the ONNX embedder. Remembers the
    vector of the last text it encoded, which for `retrieve` is the query."""

    def __init__(self, embedder: Embedder) -> None:
        self._embedder = embedder
        self._last: np.ndarray | None = None

    def encode(self, texts: list[str]) -> list[list[float]]:
        vectors = [self._embedder.encode_one(text) for text in texts]
        if vectors:
            self._last = vectors[-1]
        return [v.tolist() for v in vectors]

    def take_query(self) -> np.ndarray | None:
        """The query encoded since the last call, if any, and forget it."""
        query, self._last = self._last, None
        return query


def engine_chunks(matrix: np.ndarray, chunks: list[ChunkMeta]) -> list[Chunk]:
    """v1 Chunks in matrix row order, so the engine's chunk_index is the row.
    Built once per defense: about 1 MB for a 92-chunk document."""
    if len(chunks) != matrix.shape[0]:
        raise ValueError(f"{len(chunks)} chunks for a matrix of {matrix.shape[0]} rows")
    return [Chunk(text=c.text, embedding=row.tolist()) for c, row in zip(chunks, matrix)]


@dataclass
class SourceTracker:
    """Per defense: the retrieval score of every chunk a turn has used so far."""

    matrix: np.ndarray
    chunks: list[ChunkMeta]
    first_scores: dict[int, float] = field(default_factory=dict)

    def source_for(self, turn: ConversationTurn, query: np.ndarray | None) -> Source:
        """`query` is RecordingEncoder.take_query() right after the engine built
        `turn`: a vector when the engine retrieved, None when it reused a chunk."""
        index = turn.chunk_index
        if query is not None:
            self.first_scores[index] = float(self.matrix[index] @ query)
            origin = "new_topic"
        elif turn.panelist_archetype_key == DEVILS_ADVOCATE_KEY:
            origin = "devils_advocate"
        else:
            origin = "follow_up"
        meta = self.chunks[index]
        return Source(
            text=meta.text,
            page_start=meta.page_start + 1,  # ChunkMeta pages are 0-based PyMuPDF indices
            page_end=meta.page_end + 1,
            score=round(self.first_scores[index], 4),
            origin=origin,
        )
