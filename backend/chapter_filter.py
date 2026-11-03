"""Each domain panelist's opening question, drawn from its home chapter (v2.0 M14 D2).

M12 seated the opening round by home chapter (backend/opening_order.py), but speaker
order isn't chunk order: a panelist's question comes from whatever its focus query
retrieves, and on AniCheck the Research Problem & Objectives Reviewer's query landed on
Chapters 4-5 every time. This narrows a panelist's **first** question in a session to
the chunks of its target chapter: min(HOME_CHAPTER[key], the last detected chapter), so
a three-chapter proposal sends the Results reviewer to Chapter 3.

It works through the M6/M9 retriever seam, outside M9's routing: `ChapterRetriever`
wraps the routed retriever and adds the off-chapter chunks to `exclude_indices`, so
routing still blocks what it blocked (this only narrows, never unblocks) and near-tie
sampling runs unchanged over what's left. If nothing is left, it drops only the chapter
exclusion and retrieves again, routing and the usual exclusions intact: the expected
case is the Literature Specialist (home 2) under lit_review_access=none, whose Chapter 2
is all blocked. Follow-ups and the Devil's Advocate don't retrieve, and every later new
topic isn't an opener, so none is touched. Without a chapter map, nothing is filtered.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from academic_defense_simulator.models.session import ConversationTurn
from academic_defense_simulator.rag.embeddings import TextEncoder
from academic_defense_simulator.rag.retrieval import Chunk
from backend.opening_order import HOME_CHAPTER
from backend.page_filter import chapter_of
from backend.routing import Retriever
from backend.sessions import ChunkMeta

# What happened to an opening question's retrieval, for the log line.
Outcome = Literal["applied", "fallback_no_candidates", "not_retrieved"]


def is_opening(turns: Sequence[ConversationTurn], archetype_key: str) -> bool:
    """True when `archetype_key` hasn't asked yet this session."""
    return all(turn.panelist_archetype_key != archetype_key for turn in turns)


def target_chapter(archetype_key: str, starts: dict[int, int]) -> int:
    """The home chapter, clamped to the last chapter the document has."""
    return min(HOME_CHAPTER[archetype_key], max(starts))


def off_chapter(metas: Iterable[ChunkMeta], chapter: int, starts: dict[int, int]) -> frozenset[int]:
    """Chunk indices (engine order) whose page_start falls outside `chapter`."""
    return frozenset(i for i, meta in enumerate(metas) if chapter_of(meta.page_start, starts) != chapter)


@dataclass
class ChapterRetriever:
    """`inner` with `off_chapter` excluded too; on an empty result, `inner` alone.
    `outcome` records which, for the opening question's log line."""

    inner: Retriever
    off_chapter: frozenset[int]
    outcome: Outcome = field(default="not_retrieved")

    def __call__(
        self,
        query: str,
        chunks: list[Chunk],
        top_k: int = 1,
        *,
        embedding_model: TextEncoder,
        exclude_indices: frozenset[int] = frozenset(),
    ) -> list[tuple[int, Chunk]]:
        found = self.inner(
            query, chunks, top_k, embedding_model=embedding_model, exclude_indices=exclude_indices | self.off_chapter
        )
        if found:
            self.outcome = "applied"
            return found
        self.outcome = "fallback_no_candidates"
        return self.inner(query, chunks, top_k, embedding_model=embedding_model, exclude_indices=exclude_indices)
