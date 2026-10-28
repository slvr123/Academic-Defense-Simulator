"""Literature-review passages routed by panelist role (v2.0 M9 L2).

A panelist who reads another researcher's system in the candidate's literature review
tends to ask how the candidate built it (M7 and M8 measured this). The notes halve that
rate; this removes the case instead. Only the Literature & Theory Specialist may draw a
chunk M7's page_filter tagged as literature review (fully or partly); for every other
panelist those chunks don't exist. With ADS_LIT_REVIEW_ACCESS=none, nobody draws them.

It works through the M6 retriever seam: `RoutedRetriever` wraps any retriever with
`retrieve`'s interface and adds the blocked chunks to `exclude_indices`, so the wrapped
retriever ranks, near-tie samples or arg-maxes over the remaining chunks exactly as it
did before. The engine never sees a blocked chunk, so v1 stays unchanged. A follow-up
reuses its panelist's own earlier chunk and Devil's Advocate contests one already asked
about; neither retrieves, so neither is routed. A document with no tagged chunks blocks
nothing, and every retriever call is the one it was before routing.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Literal

from academic_defense_simulator.rag.embeddings import TextEncoder
from academic_defense_simulator.rag.retrieval import Chunk
from backend.sessions import ChunkMeta

LITERATURE_SPECIALIST_KEY = "literature_theory_specialist"

# "literature": only the Literature Specialist draws literature-review chunks; "none":
# no panelist does. Set by M9 L5's verdict (settings.lit_review_access).
LitReviewAccess = Literal["literature", "none"]

Retriever = Callable[..., list[tuple[int, Chunk]]]


def may_draw_literature(archetype_key: str, access: LitReviewAccess) -> bool:
    return access == "literature" and archetype_key == LITERATURE_SPECIALIST_KEY


def blocked_chunks(metas: Iterable[ChunkMeta], archetype_key: str, access: LitReviewAccess) -> frozenset[int]:
    """The chunk indices `archetype_key` may not retrieve: every tagged chunk, unless it
    may draw literature. `metas` are in engine order, so the position is the index."""
    if may_draw_literature(archetype_key, access):
        return frozenset()
    return frozenset(i for i, meta in enumerate(metas) if meta.attribution is not None)


@dataclass(frozen=True)
class RoutedRetriever:
    """`inner` (v1's `retrieve`, or M6's NearTieRetriever) over every chunk not in
    `blocked`. Same interface, so the engine takes it through its `retriever` parameter."""

    inner: Retriever
    blocked: frozenset[int]

    def __call__(
        self,
        query: str,
        chunks: list[Chunk],
        top_k: int = 1,
        *,
        embedding_model: TextEncoder,
        exclude_indices: frozenset[int] = frozenset(),
    ) -> list[tuple[int, Chunk]]:
        return self.inner(
            query, chunks, top_k, embedding_model=embedding_model, exclude_indices=exclude_indices | self.blocked
        )


def routed(inner: Retriever, metas: Iterable[ChunkMeta], archetype_key: str, access: LitReviewAccess) -> Retriever:
    """`inner` routed for `archetype_key`."""
    return RoutedRetriever(inner, blocked_chunks(metas, archetype_key, access))

