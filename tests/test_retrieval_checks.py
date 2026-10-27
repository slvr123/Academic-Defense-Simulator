"""v2.0 M4: the retrieval-eval metric functions on synthetic inputs (evals/retrieval_checks.py)."""

from __future__ import annotations

import numpy as np
import pytest

from backend.page_filter import front_matter_boundary, is_navigation_page
from evals.retrieval_checks import (
    embedding_coverage,
    front_matter_summary,
    near_tie_rate,
    overlap_verdict,
    pair_overlaps,
    random_overlap_baseline,
    ranking,
    replay_new_topic_turns,
    replay_seeded_turns,
    score_spread,
)

TOC = "\n".join(
    ["Table of Contents"]
    + [f"Section {n} ........................ {n * 3}" for n in range(1, 9)]
)
LIST_OF_FIGURES = "\n".join(["List of Figures"] + [f"Figure {n} Module diagram {n + 10}" for n in range(1, 9)])
BODY_CHAPTER_ONE = "CHAPTER 1\nIntroduction\nSoftware systems are complex.\nThey need care.\nMore prose here."
PROSE = "Plain prose line one.\nLine two continues.\nLine three.\nLine four.\nLine five."


# --- ranking ------------------------------------------------------------------


def test_ranking_orders_best_first_and_keeps_index_order_on_ties():
    assert ranking(np.array([0.2, 0.9, 0.5, 0.9])) == [1, 3, 2, 0]


# --- Check A --------------------------------------------------------------------


def test_pair_overlaps_counts_shared_top_k_and_same_top1():
    rankings = {
        "a": [0, 1, 2, 3, 4, 5],
        "b": [0, 2, 9, 8, 7, 1],  # shares 0 and 2 in the top 5, same best
        "c": [9, 8, 7, 6, 5, 0],  # shares nothing with a's top 5
    }
    by_pair = {(p.archetype_a, p.archetype_b): p for p in pair_overlaps(rankings)}
    assert set(by_pair) == {("a", "b"), ("a", "c"), ("b", "c")}
    assert (by_pair["a", "b"].shared_top_k, by_pair["a", "b"].same_top1) == (2, True)
    assert (by_pair["a", "c"].shared_top_k, by_pair["a", "c"].same_top1) == (0, False)
    assert (by_pair["b", "c"].shared_top_k, by_pair["b", "c"].same_top1) == (3, False)


def test_random_overlap_baseline_is_k_squared_over_n():
    assert random_overlap_baseline(50) == pytest.approx(0.5)
    assert random_overlap_baseline(5) == pytest.approx(5.0)  # two 5-sets of 5 chunks share all


def test_random_overlap_baseline_matches_a_simulation():
    rng = np.random.default_rng(0)
    draws = [len(set(rng.choice(20, 5, replace=False)) & set(rng.choice(20, 5, replace=False))) for _ in range(20000)]
    assert np.mean(draws) == pytest.approx(random_overlap_baseline(20), abs=0.03)


def test_random_overlap_baseline_rejects_too_few_chunks():
    with pytest.raises(ValueError):
        random_overlap_baseline(4)


@pytest.mark.parametrize(
    ("median", "verdict"),
    [(0, "differentiated"), (1, "differentiated"), (1.5, "partial"), (3, "partial"), (3.5, "collapsed"), (5, "collapsed")],
)
def test_overlap_verdict_follows_f6(median, verdict):
    assert overlap_verdict(median) == verdict


# --- Check B --------------------------------------------------------------------


def test_score_spread_reads_the_top_five_and_the_first_gap():
    spread = score_spread(np.array([0.10, 0.50, 0.49, 0.30, 0.20, 0.40]))
    assert spread.top_scores == pytest.approx([0.50, 0.49, 0.40, 0.30, 0.20])
    assert spread.gap_1_2 == pytest.approx(0.01)
    assert spread.near_tie


def test_a_gap_at_or_above_the_threshold_is_not_a_near_tie():
    assert not score_spread(np.array([0.5, 0.47])).near_tie
    assert not score_spread(np.array([0.5, 0.48]), threshold=0.02 - 1e-9).near_tie


def test_near_tie_rate():
    spreads = [score_spread(np.array(s)) for s in ([0.5, 0.49], [0.5, 0.3], [0.5, 0.495], [0.9, 0.1])]
    assert near_tie_rate(spreads) == 0.5


# --- Check C --------------------------------------------------------------------


def test_navigation_pages_are_tocs_and_lists_not_prose_or_short_pages():
    assert is_navigation_page(TOC)
    assert is_navigation_page(LIST_OF_FIGURES)
    assert not is_navigation_page(PROSE)
    assert not is_navigation_page(BODY_CHAPTER_ONE)
    assert not is_navigation_page("12\niv\n3")  # fewer than 5 lines


def test_boundary_skips_a_toc_that_lists_chapter_one():
    toc_with_chapter = "CHAPTER 1 ........ 1\n" + TOC
    pages = ["Title page", toc_with_chapter, PROSE, BODY_CHAPTER_ONE, PROSE]
    assert front_matter_boundary(pages) == 3


def test_boundary_accepts_introduction_and_roman_chapter_headings():
    assert front_matter_boundary([PROSE, "1. Introduction\nText"]) == 1
    assert front_matter_boundary([PROSE, "Chapter I\nThe Problem"]) == 1


def test_boundary_ignores_introduction_mid_line():
    assert front_matter_boundary([PROSE, "This introduction is mid-sentence\nText"]) is None


def test_front_matter_summary():
    # pages: 0 title, 1 toc, 2 body... boundary = 2
    chunk_pages = [(0, 1), (1, 2), (2, 3), (3, 4), (4, 4)]
    summary = front_matter_summary(chunk_pages, boundary=2, navigation_pages={1}, picks=[0, 2, 0, 4])
    assert summary.base_rate == pytest.approx(2 / 5)
    assert summary.pick_rate == pytest.approx(2 / 4)
    assert summary.ratio == pytest.approx(1.25)
    assert summary.navigation_picks == [0, 0]


def test_front_matter_ratio_is_undefined_without_front_matter():
    summary = front_matter_summary([(0, 0), (1, 1)], boundary=0, navigation_pages=set(), picks=[0])
    assert summary.base_rate == 0
    assert summary.ratio is None


def test_a_chunk_spanning_into_a_navigation_page_counts_as_a_navigation_pick():
    summary = front_matter_summary([(2, 4)], boundary=0, navigation_pages={3}, picks=[0])
    assert summary.navigation_picks == [0]


# --- Check D --------------------------------------------------------------------


def test_embedding_coverage_counts_chunks_over_the_limit_and_the_seen_share():
    coverage = embedding_coverage([128, 256, 512, 1024])
    assert coverage.chunks == 4
    assert coverage.over_limit == 2  # 256 fits exactly
    assert coverage.mean_seen_share == pytest.approx((1 + 1 + 0.5 + 0.25) / 4)
    assert coverage.token_distribution["max"] == 1024


# --- Check E --------------------------------------------------------------------


def test_replay_gives_each_archetype_its_best_unused_chunk_in_panel_order():
    panel = [
        ("tech", [4, 1, 2, 3, 0]),
        ("method", [4, 1, 0, 2, 3]),  # 4 taken -> 1
        ("results", [1, 4, 3, 0, 2]),  # 1 and 4 taken -> 3
    ]
    turns = replay_new_topic_turns(panel, max_turns=6)
    assert [(t.turn, t.archetype, t.chunk, t.displacement_rank) for t in turns] == [
        (1, "tech", 4, 1),
        (2, "method", 1, 2),
        (3, "results", 3, 3),
    ]


def test_replay_stops_at_max_turns():
    panel = [("a", [0, 1, 2]), ("b", [0, 1, 2]), ("c", [0, 1, 2])]
    assert [t.chunk for t in replay_new_topic_turns(panel, max_turns=2)] == [0, 1]


def test_replay_raises_when_the_document_is_exhausted():
    with pytest.raises(ValueError):
        replay_new_topic_turns([("a", [0]), ("b", [0])], max_turns=2)


# --- Check E, seeded (M6 H3) ----------------------------------------------------------


def test_seeded_replay_is_reproducible_and_stays_in_the_near_tie_band():
    # a: chunks 0 and 1 are near-tied; b: chunk 0 is clearly best, then 1 and 2 near-tied.
    panel = [("a", np.array([0.50, 0.49, 0.10, 0.05])), ("b", np.array([0.90, 0.60, 0.595, 0.10]))]
    first = replay_seeded_turns(panel, "session-1", max_turns=6)
    assert first == replay_seeded_turns(panel, "session-1", max_turns=6)
    assert first[0].chunk in {0, 1} and first[0].displacement_rank in {1, 2}
    # b's best (0) is either still free or taken by a; its band is then {1, 2} minus a's pick.
    assert first[1].chunk not in {first[0].chunk, 3}


def test_seeded_replay_varies_across_sessions():
    panel = [("a", np.array([0.50, 0.495, 0.49, 0.10]))]
    firsts = {replay_seeded_turns(panel, f"s{n}", max_turns=1)[0].chunk for n in range(100)}
    assert firsts == {0, 1, 2}


def test_seeded_replay_without_near_ties_is_the_top1_replay():
    panel = [("tech", np.array([0.1, 0.9, 0.5])), ("method", np.array([0.2, 0.95, 0.6]))]
    rankings = [(a, ranking(s)) for a, s in panel]
    assert replay_seeded_turns(panel, "any", max_turns=6) == replay_new_topic_turns(rankings, max_turns=6)
