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

M7 J2: with attribution notes on, a literature-review chunk's text in the engine
carries a note saying it describes cited work (M8 K2 adds the rule against asking
how the candidate built it). Only the text the engine's prompts see
changes. Retrieval ranks the session matrix, embedded at ingest from the original
text, and the source reveal reads ChunkMeta, so neither sees the note. M9 L3 adds
the researchers the chunk cites, by name, when it cites any.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field

import numpy as np

from academic_defense_simulator.models.session import ConversationTurn
from academic_defense_simulator.panel import DEVILS_ADVOCATE_KEY
from academic_defense_simulator.rag.retrieval import Chunk
from backend.embedder import Embedder
from backend.page_filter import Attribution, cited_authors
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


# M7 J2, with M8 K2's rule added (round 1's wording is in docs/v2.0-m7-results.md).
# No page numbers: the panelist would quote them back as if they were evidence.
ATTRIBUTION_NOTES: dict[Attribution, str] = {
    "literature_review": (
        "[Note to the panelist: this excerpt is from the candidate's review of related literature. "
        "It describes other researchers' work that the candidate cites, not the candidate's own study or system. "
        "Ask about how the candidate used, compared against, or departed from this work. "
        "Never ask how the candidate built, ran, scaled, deployed or implemented the system or method this "
        "excerpt describes, because the candidate didn't build it; ask why they chose it, how they compared "
        "their own work against it, or where they departed from it.]"
    ),
    "partly_literature_review": (
        "[Note to the panelist: part of this excerpt is from the candidate's review of related literature. "
        "Where it describes other researchers' work that the candidate cites, that work is not the candidate's "
        "own study or system: ask about how the candidate used, compared against, or departed from it. "
        "Never ask how the candidate built, ran, scaled, deployed or implemented a system or method the "
        "excerpt credits to other researchers; ask why they chose it, how they compared their own work "
        "against it, or where they departed from it.]"
    ),
}


# M9 L3: after the note, who the excerpt cites, so the panelist can't take a cited
# system for the candidate's. Only on a chunk that names someone.
CITED_AUTHORS_NOTE = (
    "[Researchers cited in this excerpt: {authors}. Their systems are not the candidate's. "
    "Ask how the candidate used, compared against or departed from them.]"
)


def attribution_note(meta: ChunkMeta) -> str:
    """A tagged chunk's note: M8's, then L3's list when the chunk cites anyone by name."""
    assert meta.attribution is not None
    authors = cited_authors(meta.text)
    if not authors:
        return ATTRIBUTION_NOTES[meta.attribution]
    return f"{ATTRIBUTION_NOTES[meta.attribution]}\n{CITED_AUTHORS_NOTE.format(authors=', '.join(authors))}"


def engine_text(meta: ChunkMeta, attribution_notes: bool) -> str:
    """The chunk's text as the engine's prompts see it: with its note when notes are on."""
    if not attribution_notes or meta.attribution is None:
        return meta.text
    return f"{attribution_note(meta)}\n\n{meta.text}"


def chunk_copy_bytes(chunks: list[Chunk], metas: list[ChunkMeta]) -> int:
    """What engine_chunks adds per defense: the Chunk objects, their embedding lists
    and the float objects in them, plus any annotated text. A plain text isn't
    counted; it's the ChunkMeta's own string, shared rather than copied."""
    return sum(
        sys.getsizeof(c) + sys.getsizeof(c.embedding) + sum(sys.getsizeof(x) for x in c.embedding)
        + (0 if c.text is m.text else sys.getsizeof(c.text))
        for c, m in zip(chunks, metas)
    ) + sys.getsizeof(chunks)


def engine_chunks(matrix: np.ndarray, chunks: list[ChunkMeta], *, attribution_notes: bool = False) -> list[Chunk]:
    """v1 Chunks in matrix row order, so the engine's chunk_index is the row.
    Built once per defense; chunk_copy_bytes says what it costs. `attribution_notes`
    is M7's flag (settings.attribution_notes)."""
    if len(chunks) != matrix.shape[0]:
        raise ValueError(f"{len(chunks)} chunks for a matrix of {matrix.shape[0]} rows")
    return [Chunk(text=engine_text(c, attribution_notes), embedding=row.tolist()) for c, row in zip(chunks, matrix)]


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
