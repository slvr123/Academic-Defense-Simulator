"""Pure metric functions for the v2.0 M4 retrieval eval (checks A-E).

No I/O and no framework imports: every function takes plain lists and numpy
arrays, so the tests can feed it synthetic inputs and the runner can feed it the
production chunker's and embedder's output. Scores are cosine similarities of
unit-length vectors, as `rag.retrieval.retrieve` computes them.
See docs/v2.0-m4-retrieval-eval-decisions.md, F5.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from itertools import combinations

import numpy as np

# The detector lives in production since M5; re-exported here for the runner and tests.
from backend.page_filter import front_matter_boundary, is_navigation_page  # noqa: F401
from backend.retrieval_sampling import pick_near_tie, turn_seed

NEAR_TIE_GAP = 0.02
OVERLAP_K = 5
MAX_SEQ_LENGTH = 256  # backend.embedder.MAX_SEQ_LENGTH; the runner asserts they agree


# --- shared -----------------------------------------------------------------


def ranking(scores: np.ndarray) -> list[int]:
    """Chunk indices from best to worst score. Ties keep index order."""
    return [int(i) for i in np.argsort(-scores, kind="stable")]


# --- Check A: archetype evidence overlap ------------------------------------


@dataclass(frozen=True)
class PairOverlap:
    archetype_a: str
    archetype_b: str
    shared_top_k: int
    same_top1: bool


def pair_overlaps(rankings: dict[str, list[int]], k: int = OVERLAP_K) -> list[PairOverlap]:
    """Every unordered pair of archetypes: how many of their top-k chunks they share,
    and whether their best chunk is the same one."""
    return [
        PairOverlap(
            archetype_a=a,
            archetype_b=b,
            shared_top_k=len(set(rankings[a][:k]) & set(rankings[b][:k])),
            same_top1=rankings[a][0] == rankings[b][0],
        )
        for a, b in combinations(rankings, 2)
    ]


def random_overlap_baseline(n_chunks: int, k: int = OVERLAP_K) -> float:
    """Expected overlap of two independent uniformly random k-subsets of n chunks.
    Each of one set's k members lands in the other with probability k/n."""
    if n_chunks < k:
        raise ValueError(f"need at least {k} chunks, got {n_chunks}")
    return k * k / n_chunks


def overlap_verdict(median_shared: float) -> str:
    """F6's Check A rule on the median shared count."""
    if median_shared <= 1:
        return "differentiated"
    if median_shared <= 3:
        return "partial"
    return "collapsed"


# --- Check B: score spread --------------------------------------------------


@dataclass(frozen=True)
class ScoreSpread:
    top_scores: list[float]
    gap_1_2: float
    near_tie: bool


def score_spread(scores: np.ndarray, k: int = OVERLAP_K, threshold: float = NEAR_TIE_GAP) -> ScoreSpread:
    if len(scores) < 2:
        raise ValueError("need at least 2 chunks to measure a gap")
    top = [float(scores[i]) for i in ranking(scores)[:k]]
    gap = top[0] - top[1]
    return ScoreSpread(top_scores=top, gap_1_2=gap, near_tie=gap < threshold)


def near_tie_rate(spreads: list[ScoreSpread]) -> float:
    return sum(s.near_tie for s in spreads) / len(spreads)


def distribution(values: list[float]) -> dict[str, float]:
    """min, quartiles, max and mean: enough to see a distribution's shape in a table."""
    q1, median, q3 = statistics.quantiles(values, n=4, method="inclusive")
    return {
        "min": min(values),
        "q1": q1,
        "median": median,
        "q3": q3,
        "max": max(values),
        "mean": statistics.fmean(values),
    }


# --- Check C: front matter --------------------------------------------------

@dataclass(frozen=True)
class FrontMatterSummary:
    base_rate: float
    pick_rate: float
    ratio: float | None  # None when the base rate is 0: the ratio is undefined
    navigation_picks: list[int]  # chunk indices of picks touching a navigation page


def front_matter_summary(
    chunk_pages: list[tuple[int, int]],
    boundary: int,
    navigation_pages: set[int],
    picks: list[int],
) -> FrontMatterSummary:
    """`chunk_pages` are each chunk's 0-based (page_start, page_end); a chunk is front
    matter when it starts before `boundary`. `picks` are chunk indices (repeats count)."""
    in_front = [start < boundary for start, _ in chunk_pages]
    base_rate = sum(in_front) / len(chunk_pages)
    pick_rate = sum(in_front[i] for i in picks) / len(picks)
    navigation_picks = [
        i for i in picks if any(p in navigation_pages for p in range(chunk_pages[i][0], chunk_pages[i][1] + 1))
    ]
    return FrontMatterSummary(
        base_rate=base_rate,
        pick_rate=pick_rate,
        ratio=pick_rate / base_rate if base_rate else None,
        navigation_picks=navigation_picks,
    )


# --- Check D: embedding coverage --------------------------------------------


@dataclass(frozen=True)
class Coverage:
    chunks: int
    over_limit: int
    mean_seen_share: float
    token_distribution: dict[str, float]


def embedding_coverage(token_counts: list[int], max_len: int = MAX_SEQ_LENGTH) -> Coverage:
    """`token_counts` include the [CLS] and [SEP] the embedder adds, untruncated. The
    embedder sees min(count, max_len) of them."""
    return Coverage(
        chunks=len(token_counts),
        over_limit=sum(n > max_len for n in token_counts),
        mean_seen_share=statistics.fmean(min(n, max_len) / n for n in token_counts),
        token_distribution=distribution([float(n) for n in token_counts]),
    )


# --- Check E: simulated sessions --------------------------------------------


@dataclass(frozen=True)
class ReplayTurn:
    turn: int  # 1-based
    archetype: str
    chunk: int
    displacement_rank: int  # 1-based place of `chunk` in the archetype's own ranking


def replay_new_topic_turns(panel_rankings: list[tuple[str, list[int]]], max_turns: int) -> list[ReplayTurn]:
    """The engine's new-topic path with no follow-ups: each turn the next domain
    panelist in rotation takes its best chunk not already used this session
    (`retrieve(top_k=1, exclude_indices=used)`). `select_active_panelist` gives each
    domain panelist one new topic, then hands the floor to Devil's Advocate, who
    doesn't retrieve, so a session has at most len(panel_rankings) of these turns."""
    used: set[int] = set()
    turns: list[ReplayTurn] = []
    for archetype, order in panel_rankings[:max_turns]:
        chunk = next((i for i in order if i not in used), None)
        if chunk is None:
            raise ValueError("every chunk is already used")  # the engine raises here too
        used.add(chunk)
        turns.append(ReplayTurn(turn=len(turns) + 1, archetype=archetype, chunk=chunk, displacement_rank=order.index(chunk) + 1))
    return turns


def replay_seeded_turns(panel_scores: list[tuple[str, np.ndarray]], session_id: str, max_turns: int) -> list[ReplayTurn]:
    """Check E under M6 H3's seeded near-tie sampling: as `replay_new_topic_turns`, but
    turn n picks with production's `pick_near_tie` seeded by `turn_seed(session_id, n)`.
    With no follow-ups, new-topic turn n is the defense's turn n."""
    used: set[int] = set()
    turns: list[ReplayTurn] = []
    for archetype, scores in panel_scores[:max_turns]:
        number = len(turns) + 1
        chunk = pick_near_tie(scores, frozenset(used), turn_seed(session_id, number))
        if chunk is None:
            raise ValueError("every chunk is already used")  # the engine raises here too
        used.add(chunk)
        turns.append(ReplayTurn(turn=number, archetype=archetype, chunk=chunk, displacement_rank=ranking(scores).index(chunk) + 1))
    return turns
