"""Where a question's grounding_reference sits in its passage, for the source reveal's
highlight (v2.0 M8 K6).

The decision is v1's grounding check itself (`is_grounded`): no span unless it passes.
The span is then found the way `grounding_ratio` scores: an exact match after
normalising whitespace and case, or else the best reference-sized window, stepping a
quarter of the reference at a time. Both run on the normalised text, so each
normalised character keeps the index of the original character it came from, and the
span maps back onto the passage exactly as the API sends it.

Offsets are UTF-16 code units, because that is how JavaScript indexes a string: a
passage with a character outside the Basic Multilingual Plane (a math italic letter
from a PDF, say) would otherwise put the highlight one place off per such character.
"""

from __future__ import annotations

import difflib

from academic_defense_simulator.grounding import is_grounded
from backend.schemas import Highlight


def _normalised(text: str) -> tuple[str, list[int]]:
    """v1's _normalize (collapse whitespace runs to one space, strip, lowercase), with
    the original index of every character it produces."""
    chars: list[str] = []
    origin: list[int] = []
    pending_space: int | None = None
    for index, char in enumerate(text):
        if char.isspace():
            if chars and pending_space is None:
                pending_space = index
            continue
        if pending_space is not None:
            chars.append(" ")
            origin.append(pending_space)
            pending_space = None
        for lowered in char.lower():
            chars.append(lowered)
            origin.append(index)
    return "".join(chars), origin


def _best_window(ref: str, chunk: str) -> tuple[int, int]:
    """grounding_ratio's search, returning where its best window starts and ends."""
    found = chunk.find(ref)
    if found >= 0:
        return found, found + len(ref)
    size = len(ref)
    step = max(1, size // 4)
    best, best_start = -1.0, 0
    for start in range(0, max(1, len(chunk) - size + 1), step):
        ratio = difflib.SequenceMatcher(None, ref, chunk[start : start + size]).ratio()
        if ratio > best:
            best, best_start = ratio, start
    return best_start, min(len(chunk), best_start + size)


def _utf16(text: str, index: int) -> int:
    return len(text[:index].encode("utf-16-le")) // 2


def highlight_span(reference: str, passage: str) -> Highlight | None:
    """The reference's place in `passage`, or None when the grounding check fails it."""
    if not is_grounded(reference, passage):
        return None
    ref, _ = _normalised(reference)
    chunk, origin = _normalised(passage)
    start, end = _best_window(ref, chunk)
    first, last = origin[start], origin[end - 1]
    return Highlight(start=_utf16(passage, first), end=_utf16(passage, last + 1))
