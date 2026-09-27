"""PDF chunking helpers."""

from __future__ import annotations

from dataclasses import dataclass
import re
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
    """Compatibility text-only interface for existing probes and evaluation scripts."""
    return [chunk.text for chunk in chunk_pdf_with_sources(path)]


@dataclass(frozen=True)
class LocatedChunk:
    text: str
    page_numbers: tuple[int, ...]


def chunk_pdf_with_sources(path: str | Path) -> list[LocatedChunk]:
    paragraphs = [(para, page.page_number) for page in extract_pages(path)
                  for para in _paragraphs_from_page_text(page.text)]
    return _pack_with_sources(paragraphs)


def _pack_with_sources(paragraphs: list[tuple[str, int]]) -> list[LocatedChunk]:
    result = []
    current = []
    current_len = 0

    def emit():
        result.append(LocatedChunk("\n\n".join(p for p, _ in current),
                                   tuple(dict.fromkeys(n for _, n in current))))

    for para, page in paragraphs:
        if current and current_len + len(para) > TARGET_CHARS:
            emit()
            overlap = []
            overlap_len = 0
            for item in reversed(current):
                if overlap_len + len(item[0]) > OVERLAP_CHARS:
                    break
                overlap.insert(0, item)
                overlap_len += len(item[0])
            current = overlap
            current_len = overlap_len
        current.append((para, page))
        current_len += len(para)
    if current:
        emit()
    return result


def _pack(paragraphs: list[str]) -> list[str]:
    return [chunk.text for chunk in _pack_with_sources([(p, 1) for p in paragraphs])]


CHUNKER_VERSION = "page-token-v1"
TOKEN_OVERLAP = 32


@dataclass(frozen=True)
class SourcePassage:
    text: str
    page_number: int


def extract_pages(path: str | Path) -> list[SourcePassage]:
    """Extract physical PDF pages (one-based), never guessed printed page labels."""
    import fitz

    try:
        with fitz.open(str(path)) as document:
            pages = [SourcePassage("\n\n".join(_paragraphs_from_page_text(page.get_text("text"))), i + 1)
                     for i, page in enumerate(document)]
    except Exception as exc:
        raise DocumentIngestionError("Could not open or read the uploaded document as a PDF.") from exc
    if sum(len(page.text) for page in pages) < _MIN_EXTRACTED_CHARS:
        raise DocumentIngestionError("The uploaded document has too little extractable text; it may be scanned or blank.")
    return pages


def chunk_pages(pages: list[SourcePassage], embedding_model) -> list[SourcePassage]:
    """Keep every passage within the encoder budget, including special tokens.

    Offset slicing preserves original spelling and whitespace, unlike decode().
    Pages never mix, so each returned passage has an exact source page.
    """
    tokenizer = embedding_model.tokenizer
    budget = embedding_model.max_seq_length - tokenizer.num_special_tokens_to_add(pair=False)
    if budget <= TOKEN_OVERLAP:
        raise ValueError("Encoder token budget must exceed the overlap.")
    passages = []
    for page in pages:
        offsets = tokenizer(page.text, add_special_tokens=False, truncation=False,
                            return_offsets_mapping=True)["offset_mapping"]
        start = 0
        while start < len(offsets):
            end = min(start + budget, len(offsets))
            text = page.text[offsets[start][0]:offsets[end - 1][1]]
            # A substring beginning inside a word can retokenize differently.
            while len(tokenizer.encode(text, add_special_tokens=True, truncation=False)) > embedding_model.max_seq_length:
                end -= 1
                if end <= start:
                    raise ValueError("Unable to fit source text within the encoder budget.")
                text = page.text[offsets[start][0]:offsets[end - 1][1]]
            passages.append(SourcePassage(text, page.page_number))
            if end == len(offsets):
                break
            start = max(start + 1, end - TOKEN_OVERLAP)
    return passages
