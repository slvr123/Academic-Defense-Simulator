"""Retrieval tests (Task 3, item 3): exclude_indices + top-k ordering.

Uses hand-built 2-D embeddings and a fake embedder injected through retrieve()'s
`embedding_model` seam, so sentence-transformers is never loaded.
"""

from __future__ import annotations

from academic_defense_simulator.rag.retrieval import Chunk, retrieve


class _FakeEmbedder:
    """Duck-types EmbeddingModel.encode: takes a [query] list, returns [query_vector]."""

    def __init__(self, query_vec):
        self._query_vec = query_vec

    def encode(self, texts):
        assert len(texts) == 1  # retrieve embeds exactly the single query string
        return [self._query_vec]


# Scores against query [1, 0] are the x-components: c0=1.0, c1=0.0, c2=0.6, c3=0.3.
_CHUNKS = [
    Chunk(text="c0", embedding=[1.0, 0.0]),
    Chunk(text="c1", embedding=[0.0, 1.0]),
    Chunk(text="c2", embedding=[0.6, 0.8]),
    Chunk(text="c3", embedding=[0.3, 0.95]),
]
_QUERY_VEC = [1.0, 0.0]


def _retrieve(**kwargs):
    return retrieve("query text is ignored by the fake", _CHUNKS, embedding_model=_FakeEmbedder(_QUERY_VEC), **kwargs)


def test_top_k_orders_by_descending_score():
    results = _retrieve(top_k=4)
    assert [i for i, _ in results] == [0, 2, 3, 1]


def test_default_top_k_returns_single_best():
    results = _retrieve()
    assert len(results) == 1
    assert results[0][0] == 0
    assert results[0][1].text == "c0"


def test_exclude_indices_are_removed_and_original_indices_preserved():
    results = _retrieve(top_k=3, exclude_indices=frozenset({0}))
    returned = [i for i, _ in results]
    assert 0 not in returned
    assert returned == [2, 3, 1]  # next-best after excluding the winner


def test_excluding_all_candidates_returns_empty():
    results = _retrieve(top_k=3, exclude_indices=frozenset({0, 1, 2, 3}))
    assert results == []


def test_top_k_larger_than_pool_returns_all():
    results = _retrieve(top_k=99)
    assert len(results) == len(_CHUNKS)
