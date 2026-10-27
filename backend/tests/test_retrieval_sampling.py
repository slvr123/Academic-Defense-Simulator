"""v2.0 M6 H3: seeded near-tie sampling (backend/retrieval_sampling.py)."""

from __future__ import annotations

import hashlib

import numpy as np
import pytest

from academic_defense_simulator.rag.retrieval import Chunk, retrieve
from backend.retrieval_sampling import NearTieRetriever, near_tie_band, pick_near_tie, turn_seed

# Against the query [1, 0]: 0.90, 0.89, 0.885, 0.70, 0.30 for chunks 0-4.
_SCORES = np.array([0.90, 0.89, 0.885, 0.70, 0.30])


def _unit(x: float) -> list[float]:
    return [x, float(np.sqrt(1 - x * x))]


_CHUNKS = [Chunk(text=f"c{i}", embedding=_unit(float(s))) for i, s in enumerate(_SCORES)]


class _Encoder:
    def __init__(self):
        self.texts: list[str] = []

    def encode(self, texts):
        self.texts += texts
        return [[1.0, 0.0] for _ in texts]


# --- seeds ----------------------------------------------------------------------


def test_turn_seed_is_stable_and_differs_by_session_and_turn():
    assert turn_seed("s", 1) == turn_seed("s", 1)
    assert len({turn_seed("s", 1), turn_seed("s", 2), turn_seed("t", 1)}) == 3
    # Fixed across processes and Python versions: sha256, not hash().
    assert turn_seed("session", 1) == int.from_bytes(hashlib.sha256(b"session:1").digest()[:8], "big")


# --- the band -------------------------------------------------------------------


def test_the_band_is_the_chunks_under_the_gap_below_the_best_best_first():
    assert near_tie_band(_SCORES, frozenset()) == [0, 1, 2]


def test_a_gap_of_exactly_the_threshold_is_outside_the_band():
    assert near_tie_band(np.array([0.5, 0.48, 0.49]), frozenset(), gap=0.02 + 1e-12) == [0, 2, 1]
    assert near_tie_band(np.array([0.5, 0.25]), frozenset()) == [0]


def test_excluded_chunks_leave_the_band_and_the_best_is_recomputed():
    assert near_tie_band(_SCORES, frozenset({0})) == [1, 2]
    assert near_tie_band(_SCORES, frozenset({0, 1, 2})) == [3]
    assert near_tie_band(_SCORES, frozenset(range(5))) == []


# --- the pick -------------------------------------------------------------------


def test_the_pick_is_fixed_by_the_seed():
    assert {pick_near_tie(_SCORES, frozenset(), 42) for _ in range(20)} == {pick_near_tie(_SCORES, frozenset(), 42)}


def test_every_seed_picks_inside_the_band_and_the_band_gets_used():
    picks = {pick_near_tie(_SCORES, frozenset(), turn_seed("s", n)) for n in range(200)}
    assert picks == {0, 1, 2}


def test_without_a_near_tie_the_pick_is_the_argmax():
    scores = np.array([0.2, 0.9, 0.5])
    assert {pick_near_tie(scores, frozenset(), seed) for seed in range(50)} == {1}


def test_nothing_left_is_none():
    assert pick_near_tie(_SCORES, frozenset(range(5)), 1) is None


# --- the retriever ----------------------------------------------------------------


def test_the_retriever_embeds_the_query_once_and_returns_a_band_chunk():
    encoder = _Encoder()
    [(index, chunk)] = NearTieRetriever(seed=7)("the query", _CHUNKS, embedding_model=encoder)
    assert encoder.texts == ["the query"]  # RecordingEncoder relies on this
    assert index in {0, 1, 2} and chunk is _CHUNKS[index]
    assert index == pick_near_tie(_SCORES, frozenset(), 7)


def test_the_retriever_respects_exclusion_like_retrieve():
    excluded = frozenset({0, 1, 2})
    assert NearTieRetriever(seed=1)("q", _CHUNKS, embedding_model=_Encoder(), exclude_indices=excluded) == retrieve(
        "q", _CHUNKS, embedding_model=_Encoder(), exclude_indices=excluded
    )
    assert NearTieRetriever(seed=1)("q", _CHUNKS, embedding_model=_Encoder(), exclude_indices=frozenset(range(5))) == []


def test_with_no_near_ties_the_retriever_agrees_with_retrieve():
    chunks = [Chunk(text=f"c{i}", embedding=_unit(s)) for i, s in enumerate([0.2, 0.9, 0.5])]
    for seed in range(20):
        assert NearTieRetriever(seed)("q", chunks, embedding_model=_Encoder()) == retrieve(
            "q", chunks, embedding_model=_Encoder()
        )


def test_the_retriever_only_picks_one():
    with pytest.raises(ValueError):
        NearTieRetriever(seed=1)("q", _CHUNKS, top_k=2, embedding_model=_Encoder())
