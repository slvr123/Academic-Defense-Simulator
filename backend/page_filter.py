"""Front-matter navigation pages: detected, then left out of chunking (v2.0 M5).

A table of contents or list of figures names every topic in the paper, so it
out-scores real content for nearly every panelist's focus query (M4 Check C).
The detector is the one M4 measured, moved here unchanged from
evals/retrieval_checks.py, which now imports it. See
docs/v2.0-m5-navigation-filter-decisions.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

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
    removed_pages: list[int]  # 0-based, ascending


def chunk_pdf_without_navigation(path: str | Path) -> FilteredChunks:
    """v1's `chunk_pdf_with_pages`, reading every page except `removable_pages`.
    Page indices stay those of the original PDF, and the scanned-PDF check counts
    every page, removed ones included. With nothing removed, the chunks are v1's."""
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
    paragraphs: list[str] = []
    paragraph_pages: list[int] = []
    for page_index, text in enumerate(page_texts):
        if page_index in removed:
            continue
        page_paragraphs = _paragraphs_from_page_text(text)
        paragraphs.extend(page_paragraphs)
        paragraph_pages.extend([page_index] * len(page_paragraphs))

    chunks = [
        PageChunk(
            text="\n\n".join(paragraphs[start:end]),
            page_start=paragraph_pages[start],
            page_end=paragraph_pages[end - 1],
        )
        for start, end in _pack_spans([len(p) for p in paragraphs])
    ]
    return FilteredChunks(chunks=chunks, removed_pages=sorted(removed))
