"""Standing hallucination detector for panelist question grounding.

A `PanelistQuestion.grounding_reference` is meant to be lifted from the excerpt the
question was generated over. This module verifies that claim: a reference that does not
actually appear in its chunk is a hallucinated citation. Pure functions only — no
logging, no session state, no LLM calls. The loop owns the decision of what to do on a
failure (warn, never crash); this module only measures.

See `docs/v0.2.5-hardening-brief.md` Task 2. Reused by the tests (Task 3) and the
question-generation probe (Task 4).
"""

from __future__ import annotations

import difflib
import re


def _normalize(text: str) -> str:
    """Lowercase, collapse every whitespace run to a single space, strip the ends.

    Grounding should be robust to formatting drift (a line break the model dropped,
    doubled spaces from PDF extraction), not to genuine paraphrase — so normalization
    stops at whitespace and case.
    """
    return re.sub(r"\s+", " ", text).strip().lower()


def grounding_ratio(reference: str, chunk_text: str) -> float:
    """Best normalized similarity between `reference` and any reference-sized window of
    `chunk_text`, in [0.0, 1.0]. Returns 1.0 on an exact normalized-substring match.

    The probe records this to see how close a non-exact match came; `is_grounded` is the
    boolean gate built on top of it.
    """
    ref = _normalize(reference)
    chunk = _normalize(chunk_text)

    # Exact substring is the expected common case (Day 2 showed verbatim references) and
    # the cheap path — take it before any windowed comparison.
    if ref in chunk:
        return 1.0

    window_size = len(ref)
    step = max(1, window_size // 4)

    # Slide a reference-sized window across the chunk and keep the closest match. The
    # `max(1, ...)` guard yields a single window (the whole, shorter chunk) when the
    # reference is longer than the chunk, so ratio is still defined in that case.
    best = 0.0
    for start in range(0, max(1, len(chunk) - window_size + 1), step):
        window = chunk[start : start + window_size]
        ratio = difflib.SequenceMatcher(None, ref, window).ratio()
        if ratio > best:
            best = ratio
    return best


def is_grounded(reference: str, chunk_text: str, threshold: float = 0.85) -> bool:
    """True if `reference` appears in `chunk_text`, exactly or fuzzily above `threshold`.

    An empty reference is never grounded — a blank string is trivially a substring of
    everything, which would silently pass a hallucinated (missing) citation.
    """
    if not _normalize(reference):
        return False
    return grounding_ratio(reference, chunk_text) >= threshold
