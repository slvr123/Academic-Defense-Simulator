"""PDF chunking helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# Public (v1.0.1 Decision 3): the sample sidecar's staleness guard asserts its
# recorded chunker parameters against these live values, so they are part of the
# ingestion contract now, not module-private trivia. `_MIN_EXTRACTED_CHARS` stays
# private — it gates bad input, it does not shape the chunks a sidecar recorded.
TARGET_CHARS = 2800   # ~700 tokens * 4 chars/token (midpoint of 600-800 range)
OVERLAP_CHARS = 400   # ~100 tokens * 4 chars/token
_MIN_EXTRACTED_CHARS = 500  # below this, treat the doc as blank/scanned-image/corrupt

# Miss 3 (v0.3 hardening, Decision 8): a page-number footer that PyMuPDF extracts as its
# own PARAGRAPH (bounded by a blank line on both sides) -- confirmed via a fresh DAZSMA
# extraction to be the real corruption pattern, e.g. a run of scanned/image-only appendix
# pages leaving nothing behind but five consecutive standalone "175"/"176"/.../"179"
# paragraphs. Matched at the whole-paragraph level, not per-line: a table's quantity/price
# cells (e.g. "1" \n "₱1,810.00") are lines *within* one larger multi-line paragraph, never
# their own paragraph, so this never touches them -- a naive per-line strip would.
_PAGE_NUMBER_PARAGRAPH = re.compile(r"^\d{1,4}$")


class DocumentIngestionError(Exception):
    """Raised when an uploaded document can't be opened or yields too little
    extractable text. Bad input, not a transient fault — no retry."""


class InsufficientTextError(DocumentIngestionError):
    """The document opened but yielded too little text — blank, scanned-image-only,
    or corrupted. A subclass so v1 callers catching the base class are unaffected,
    while the v2 backend can tell "looks scanned" apart from "not a readable PDF"."""

    def __init__(self, message: str, total_chars: int) -> None:
        super().__init__(message)
        self.total_chars = total_chars


@dataclass(frozen=True)
class PageChunk:
    """One chunk plus the 0-based PyMuPDF page indices its first and last
    paragraphs came from (v2.0 M1). Never parsed from the text itself."""

    text: str
    page_start: int
    page_end: int


def _paragraphs_from_page_text(text: str) -> list[str]:
    """Split one page's raw extracted text into paragraphs, dropping page-number-footer
    artifacts (Miss 3 / Decision 8). `re.split` on a whitespace-tolerant blank-line
    pattern, not a literal `"\\n\\n"` split: PyMuPDF sometimes emits a "blank" line
    carrying a stray space/tab (`"\\n \\n"`), which a literal split misses, leaving a lone
    page-number paragraph glued to real preceding text (confirmed via a fresh DAZSMA
    extraction). This can only split more finely than before, never merge previously-
    distinct paragraphs, so it carries no risk to real content."""
    return [
        p.strip()
        for p in re.split(r"\n\s*\n", text)
        if p.strip() and not _PAGE_NUMBER_PARAGRAPH.match(p.strip())
    ]


def chunk_pdf(path: str | Path) -> list[str]:
    return [chunk.text for chunk in chunk_pdf_with_pages(path)]


def chunk_pdf_with_pages(path: str | Path) -> list[PageChunk]:
    """`chunk_pdf` plus each chunk's page range. Same paragraphs, same packing:
    `chunk_pdf` is this with the page indices dropped, so the two can't drift."""
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:
        raise RuntimeError("PyMuPDF is required for PDF chunking") from exc

    try:
        document = fitz.open(str(path))
        paragraphs: list[str] = []
        paragraph_pages: list[int] = []
        total_chars = 0
        for page_index, page in enumerate(document):
            text = page.get_text("text")
            total_chars += len(text)
            page_paragraphs = _paragraphs_from_page_text(text)
            paragraphs.extend(page_paragraphs)
            paragraph_pages.extend([page_index] * len(page_paragraphs))
    except Exception as exc:
        # Deliberately not interpolating `exc` into the message: PyMuPDF's own exception
        # text embeds the filesystem path it was given (e.g. a temp path on the server),
        # which would leak into this user-facing message. The original exception is still
        # chained via `from exc` for anyone reading server-side logs/tracebacks.
        raise DocumentIngestionError(
            "Could not open or read the uploaded document as a PDF."
        ) from exc

    if total_chars < _MIN_EXTRACTED_CHARS:
        raise InsufficientTextError(
            f"The uploaded document yielded only {total_chars} characters of extractable "
            f"text (minimum {_MIN_EXTRACTED_CHARS}) — it may be blank, scanned-image-only, "
            "or corrupted.",
            total_chars=total_chars,
        )

    return [
        PageChunk(
            text="\n\n".join(paragraphs[start:end]),
            page_start=paragraph_pages[start],
            page_end=paragraph_pages[end - 1],
        )
        for start, end in _pack_spans([len(p) for p in paragraphs])
    ]


def _pack(paragraphs: list[str]) -> list[str]:
    return [
        "\n\n".join(paragraphs[start:end])
        for start, end in _pack_spans([len(p) for p in paragraphs])
    ]


def _pack_spans(lengths: list[int]) -> list[tuple[int, int]]:
    """Greedy packing over paragraph lengths, returned as half-open index spans
    into the paragraph list. Spans overlap when tail paragraphs carry back."""
    spans: list[tuple[int, int]] = []
    start = 0
    current_len = 0

    for index, para_len in enumerate(lengths):
        if index > start and current_len + para_len > TARGET_CHARS:
            spans.append((start, index))
            # carry back overlap paragraphs from the tail
            new_start = index
            overlap_len = 0
            for tail in range(index - 1, start - 1, -1):
                if overlap_len + lengths[tail] > OVERLAP_CHARS:
                    break
                new_start = tail
                overlap_len += lengths[tail]
            start = new_start
            current_len = overlap_len
        current_len += para_len

    if start < len(lengths):
        spans.append((start, len(lengths)))

    return spans
