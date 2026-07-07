"""PDF chunking helpers."""

from __future__ import annotations

from pathlib import Path

_TARGET_CHARS = 2800   # ~700 tokens * 4 chars/token (midpoint of 600-800 range)
_OVERLAP_CHARS = 400   # ~100 tokens * 4 chars/token
_MIN_EXTRACTED_CHARS = 500  # below this, treat the doc as blank/scanned-image/corrupt


class DocumentIngestionError(Exception):
    """Raised when an uploaded document can't be opened or yields too little
    extractable text. Bad input, not a transient fault — no retry."""


def chunk_pdf(path: str | Path) -> list[str]:
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:
        raise RuntimeError("PyMuPDF is required for PDF chunking") from exc

    try:
        document = fitz.open(str(path))
        paragraphs: list[str] = []
        total_chars = 0
        for page in document:
            text = page.get_text("text")
            total_chars += len(text)
            paragraphs.extend(
                p.strip() for p in text.split("\n\n") if p.strip()
            )
    except Exception as exc:
        # Deliberately not interpolating `exc` into the message: PyMuPDF's own exception
        # text embeds the filesystem path it was given (e.g. a temp path on the server),
        # which would leak into this user-facing message. The original exception is still
        # chained via `from exc` for anyone reading server-side logs/tracebacks.
        raise DocumentIngestionError(
            "Could not open or read the uploaded document as a PDF."
        ) from exc

    if total_chars < _MIN_EXTRACTED_CHARS:
        raise DocumentIngestionError(
            f"The uploaded document yielded only {total_chars} characters of extractable "
            f"text (minimum {_MIN_EXTRACTED_CHARS}) — it may be blank, scanned-image-only, "
            "or corrupted."
        )

    return _pack(paragraphs)


def _pack(paragraphs: list[str]) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for para in paragraphs:
        para_len = len(para)
        if current and current_len + para_len > _TARGET_CHARS:
            chunks.append("\n\n".join(current))
            # carry back overlap paragraphs from the tail
            overlap: list[str] = []
            overlap_len = 0
            for p in reversed(current):
                if overlap_len + len(p) > _OVERLAP_CHARS:
                    break
                overlap.insert(0, p)
                overlap_len += len(p)
            current = overlap
            current_len = overlap_len
        current.append(para)
        current_len += para_len

    if current:
        chunks.append("\n\n".join(current))

    return chunks
