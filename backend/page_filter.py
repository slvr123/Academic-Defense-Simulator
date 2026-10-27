"""Pages and passages left out of chunking: front-matter navigation pages (v2.0 M5)
and the reference list (M6 H1). Plus one thing ingest marks rather than removes:
which chunks come from the literature review (M7 J2).

A table of contents or list of figures names every topic in the paper, so it
out-scores real content for nearly every panelist's focus query (M4 Check C); a
reference list of paper titles does the same from the back (M5's finding).
The navigation detector is the one M4 measured, moved here unchanged from
evals/retrieval_checks.py, which imports it. See
docs/v2.0-m5-navigation-filter-decisions.md and
docs/v2.0-m6-retrieval-reliability-voice-decisions.md and
docs/v2.0-m7-question-attribution-decisions.md.
"""

from __future__ import annotations

import bisect
import itertools
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

# v1's own splitting, packing and errors, imported rather than copied, so the two
# chunkers can only differ in which pages they read (G4).
from academic_defense_simulator.rag.chunking import (
    _MIN_EXTRACTED_CHARS,
    DocumentIngestionError,
    InsufficientTextError,
    PageChunk,
    _pack_spans,
    _paragraphs_from_page_text,
)

# A line that ends in a page number (arabic or roman), or that carries dotted leaders.
_PAGE_REF_LINE = re.compile(r"(\.{3,}|…)|(^|\s)([0-9]{1,3}|[ivxlcIVXLC]{1,7})$")
# A Chapter 1 or Introduction heading at the start of a line. A table of contents
# matches this too, which is why navigation pages are skipped when finding the boundary.
_CHAPTER_ONE_HEADING = re.compile(r"(?i)^(chapter\s+(1|i|one)\b|introduction\b|1\.?\s+introduction\b)")
NAVIGATION_MIN_LINES = 5

# H1. An optional section number: "5", "5.1", "V", followed by "." or ")" and a space.
_SECTION_NUMBER = r"(?:(?:\d{1,2}(?:\.\d{1,2})*|[ivxlc]{1,6})[.)]?\s+)?"
# A line that is only a references heading. Anchored at both ends, so a contents
# line ("References ........ 80") never matches.
_REFERENCES_HEADING = re.compile(
    rf"(?i)^{_SECTION_NUMBER}(?:references|bibliography|literature\s+cited|works\s+cited)[.:]?$"
)
# What ends the reference list: an appendix, CV or glossary heading, optionally
# lettered or numbered, and optionally titled after a dash or colon ("Appendix A –
# Survey Instrument"): appendices are often headed that way, and missing one would
# delete every appendix after it.
_BACK_MATTER_END_HEADING = re.compile(
    rf"(?i)^{_SECTION_NUMBER}(?:appendix(?:\s+[a-z0-9]{{1,4}})?(?P<title>\s*[-–:.]\s*\S.*)?|appendices|curriculum\s+vitae|glossary)[.:]?$"
)
# Dotted leaders, or a title ending in a page number, make it a contents line, not a heading.
_DOTTED_LEADERS = re.compile(r"\.{3,}|…")
_TRAILING_PAGE_NUMBER = re.compile(r"\s\d{1,3}$")

# M7 J2. A Chapter 2 or Chapter 3 heading, bare or titled. A title must follow a
# separator or be in capitals, so a line that starts a sentence ("Chapter 2 presents
# the related literature") isn't taken for the heading.
_CHAPTER_HEADING = r"^(?i:chapter)\s+(?i:{numbers})(?:[.:]|(?P<title>\s*[-–:.]\s*\S.*|\s+[^a-z]*[A-Z][^a-z]*))?$"
_CHAPTER_TWO_HEADING = re.compile(_CHAPTER_HEADING.format(numbers="2|ii|two"))
_CHAPTER_THREE_HEADING = re.compile(_CHAPTER_HEADING.format(numbers="3|iii|three"))
# A line that is only a literature-review heading, or only "Methodology".
_LITERATURE_REVIEW_HEADING = re.compile(
    rf"(?i)^{_SECTION_NUMBER}(?:review\s+of\s+related\s+literature|literature\s+review|related\s+works|related\s+literature)[.:]?$"
)
_METHODOLOGY_HEADING = re.compile(rf"(?i)^{_SECTION_NUMBER}methodology[.:]?$")

# Which note a chunk's text gets in the engine's prompt (backend/engine_adapter.py).
Attribution = Literal["literature_review", "partly_literature_review"]


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


def _is_back_matter_end(line: str) -> bool:
    match = _BACK_MATTER_END_HEADING.match(line)
    if match is None or _DOTTED_LEADERS.search(line):
        return False
    # "Appendix 1" is a heading; "Appendix A - Survey 85" is a contents line.
    return not (match.group("title") and _TRAILING_PAGE_NUMBER.search(line))


@dataclass(frozen=True)
class ReferenceRange:
    """The reference list, as 0-based (page, line) positions; lines are those of
    `page_text.splitlines()`. Removed: from the start heading up to, not including,
    the end heading, or to the end of the document when `end` is None."""

    start: tuple[int, int]
    end: tuple[int, int] | None


def reference_range(page_texts: list[str]) -> ReferenceRange | None:
    """H1: the first references heading after the front-matter boundary, accepted only
    in the document's last half; None (nothing removed) otherwise, or with no boundary."""
    boundary = front_matter_boundary(page_texts)
    if boundary is None:
        return None
    start = _first_line(page_texts, (boundary, 0), lambda line: bool(_REFERENCES_HEADING.match(line)))
    if start is None or start[0] < len(page_texts) / 2:
        return None
    return ReferenceRange(start=start, end=_first_line(page_texts, (start[0], start[1] + 1), _is_back_matter_end))


def _first_line(page_texts: list[str], after: tuple[int, int], matches: Callable[[str], bool]) -> tuple[int, int] | None:
    """The first (page, line) at or after `after` whose stripped text `matches`."""
    for page_index in range(after[0], len(page_texts)):
        lines = page_texts[page_index].splitlines()
        first = after[1] if page_index == after[0] else 0
        for line_index in range(first, len(lines)):
            if matches(lines[line_index].strip()):
                return page_index, line_index
    return None


def trim_references(page_texts: list[str], references: ReferenceRange | None) -> list[str]:
    """Page texts with the reference range cut out. Text above the heading on its page
    stays, and so does the end heading's page from the heading on."""
    if references is None:
        return list(page_texts)
    start_page, start_line = references.start
    end_page, end_line = references.end or (len(page_texts), 0)
    trimmed = []
    for page_index, text in enumerate(page_texts):
        if page_index < start_page or page_index > end_page:
            trimmed.append(text)
            continue
        lines = text.splitlines(keepends=True)
        kept_before = lines[:start_line] if page_index == start_page else []
        kept_after = lines[end_line:] if page_index == end_page else []
        trimmed.append("".join(kept_before + kept_after))
    return trimmed


def _is_chapter_heading(pattern: re.Pattern[str], line: str) -> bool:
    match = pattern.match(line)
    if match is None or _DOTTED_LEADERS.search(line):
        return False
    # "CHAPTER 2 REVIEW OF RELATED LITERATURE 13" is a contents line.
    return not (match.group("title") and _TRAILING_PAGE_NUMBER.search(line))


def _is_literature_review_start(line: str) -> bool:
    return _is_chapter_heading(_CHAPTER_TWO_HEADING, line) or bool(_LITERATURE_REVIEW_HEADING.match(line))


def _is_literature_review_end(line: str) -> bool:
    return _is_chapter_heading(_CHAPTER_THREE_HEADING, line) or bool(_METHODOLOGY_HEADING.match(line))


@dataclass(frozen=True)
class LiteratureReviewRange:
    """J2: the literature review, as 0-based (page, line) positions with lines those of
    `page_text.splitlines()`: from the start heading up to, not including, the end heading."""

    start: tuple[int, int]
    end: tuple[int, int]

    def __contains__(self, position: tuple[int, int]) -> bool:
        return self.start <= position < self.end


def literature_review_range(page_texts: list[str]) -> LiteratureReviewRange | None:
    """The first Chapter 2 or literature-review heading after the front-matter boundary,
    up to the next Chapter 3 or Methodology heading. None with no boundary, no start, or
    a start with no end: then nothing is tagged (fail-open)."""
    boundary = front_matter_boundary(page_texts)
    if boundary is None:
        return None
    start = _first_line(page_texts, (boundary, 0), _is_literature_review_start)
    if start is None:
        return None
    end = _first_line(page_texts, (start[0], start[1] + 1), _is_literature_review_end)
    return None if end is None else LiteratureReviewRange(start=start, end=end)


def _paragraph_lines(text: str, paragraphs: list[str]) -> list[int]:
    """The line of `text.splitlines()` that each of `paragraphs` ends on. `paragraphs`
    is v1's `_paragraphs_from_page_text(text)`: stripped slices of the text, in order,
    so each is found by searching on from where the one before ended."""
    line_starts = list(itertools.accumulate((len(line) for line in text.splitlines(keepends=True)), initial=0))
    lines, cursor = [], 0
    for paragraph in paragraphs:
        cursor = text.index(paragraph, cursor) + len(paragraph)
        lines.append(bisect.bisect_right(line_starts, cursor - 1) - 1)
    return lines


def _attribution(first: tuple[int, int], last: tuple[int, int], review: LiteratureReviewRange | None) -> Attribution | None:
    """J2's tag for a chunk whose first and last paragraphs end at `first` and `last`.
    A paragraph belongs to the section it ends in: a page's paragraph often opens with
    the page number or a running header, above the chapter heading it carries."""
    if review is None:
        return None
    inside = (first in review) + (last in review)
    if inside == 2:
        return "literature_review"
    return "partly_literature_review" if inside == 1 else None


def removable_pages(page_texts: list[str]) -> set[int]:
    """0-based indices of navigation pages before the front-matter boundary. With no
    boundary, nothing: the filter fails open and the document chunks as before. Pages
    at or after the boundary are never removed, whatever they look like."""
    boundary = front_matter_boundary(page_texts)
    if boundary is None:
        return set()
    return {index for index, text in enumerate(page_texts[:boundary]) if is_navigation_page(text)}


@dataclass(frozen=True)
class FilteredChunks:
    chunks: list[PageChunk]
    removed_pages: list[int]  # 0-based navigation pages, ascending
    references: ReferenceRange | None
    literature_review: LiteratureReviewRange | None
    attributions: list[Attribution | None]  # parallel to chunks


def chunk_pdf_filtered(path: str | Path, *, trim_reference_list: bool = True) -> FilteredChunks:
    """v1's `chunk_pdf_with_pages`, reading every page except `removable_pages`, with
    the reference list trimmed from the page text before v1's paragraph splitter runs
    (`trim_reference_list=False` is M5's chunker, kept for the eval's before run).
    Page indices stay those of the original PDF, and the scanned-PDF check counts all
    the original text. With nothing removed, the chunks are v1's. The literature review
    (M7 J2) is found in the text as chunked, and only tags chunks: it never changes one."""
    import fitz  # PyMuPDF

    try:
        # Closed on exit, as v1 does: the embedder runs next.
        with fitz.open(str(path)) as document:
            page_texts = [page.get_text("text") for page in document]
    except Exception as exc:
        # As in v1: PyMuPDF's message embeds the file path, so it stays out of this one.
        raise DocumentIngestionError("Could not open or read the uploaded document as a PDF.") from exc

    total_chars = sum(len(text) for text in page_texts)
    if total_chars < _MIN_EXTRACTED_CHARS:
        raise InsufficientTextError(
            f"The uploaded document yielded only {total_chars} characters of extractable "
            f"text (minimum {_MIN_EXTRACTED_CHARS}); it may be blank, scanned-image-only, "
            "or corrupted.",
            total_chars=total_chars,
        )

    removed = removable_pages(page_texts)
    references = reference_range(page_texts) if trim_reference_list else None
    chunked_texts = trim_references(page_texts, references)
    review = literature_review_range(chunked_texts)
    paragraphs: list[str] = []
    paragraph_pages: list[int] = []
    paragraph_positions: list[tuple[int, int]] = []
    for page_index, text in enumerate(chunked_texts):
        if page_index in removed:
            continue
        page_paragraphs = _paragraphs_from_page_text(text)
        paragraphs.extend(page_paragraphs)
        paragraph_pages.extend([page_index] * len(page_paragraphs))
        paragraph_positions.extend((page_index, line) for line in _paragraph_lines(text, page_paragraphs))

    spans = _pack_spans([len(p) for p in paragraphs])
    chunks = [
        PageChunk(
            text="\n\n".join(paragraphs[start:end]),
            page_start=paragraph_pages[start],
            page_end=paragraph_pages[end - 1],
        )
        for start, end in spans
    ]
    return FilteredChunks(
        chunks=chunks,
        removed_pages=sorted(removed),
        references=references,
        literature_review=review,
        attributions=[_attribution(paragraph_positions[start], paragraph_positions[end - 1], review) for start, end in spans],
    )
