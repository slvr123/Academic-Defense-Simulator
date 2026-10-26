"""Pure metric functions for the v2.0 M4 retrieval eval (checks A-E).

No I/O and no framework imports: every function takes plain lists and numpy
arrays, so the tests can feed it synthetic inputs and the runner can feed it the
production chunker's and embedder's output. Scores are cosine similarities of
unit-length vectors, as `rag.retrieval.retrieve` computes them.
See docs/v2.0-m4-retrieval-eval-decisions.md, F5.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass
from itertools import combinations

import numpy as np

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

# A line that ends in a page number (arabic or roman), or that carries dotted leaders.
_PAGE_REF_LINE = re.compile(r"(\.{3,}|…)|(^|\s)([0-9]{1,3}|[ivxlcIVXLC]{1,7})$")
# A Chapter 1 or Introduction heading at the start of a line. A table of contents
# matches this too, which is why navigation pages are skipped when finding the boundary.
_CHAPTER_ONE_HEADING = re.compile(r"(?i)^(chapter\s+(1|i|one)\b|introduction\b|1\.?\s+introduction\b)")
NAVIGATION_MIN_LINES = 5


def _lines(page_text: str) -> list[str]:
    return [line.strip() for line in page_text.splitlines() if line.strip()]


def is_navigation_page(page_text: str) -> bool:
    """A table of contents or list of figures/tables: most lines end in a page number
    or carry dotted leaders. Pages with fewer than NAVIGATION_MIN_LINES lines never are,
    so a title page whose only line is a page number doesn't qualify."""
    lines = _lines(page_text)
    if len(lines) < NAVIGATION_MIN_LINES:
        return False
    return sum(bool(_PAGE_REF_LINE.search(line)) for line in lines) > len(lines) / 2


def front_matter_boundary(page_texts: list[str]) -> int | None:
    """0-based index of the first non-navigation page with a Chapter 1 / Introduction
    heading line; every page before it is front matter. None if no page has one."""
    for index, text in enumerate(page_texts):
        if is_navigation_page(text):
            continue
        if any(_CHAPTER_ONE_HEADING.match(line) for line in _lines(text)):
            return index
    return None


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
