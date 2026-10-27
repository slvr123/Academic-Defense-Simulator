"""Front-matter navigation pages: detected, then left out of chunking (v2.0 M5).

A table of contents or list of figures names every topic in the paper, so it
out-scores real content for nearly every panelist's focus query (M4 Check C).
The detector is the one M4 measured, moved here unchanged from
evals/retrieval_checks.py, which now imports it. See
docs/v2.0-m5-navigation-filter-decisions.md.
"""

from __future__ import annotations

import re

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
