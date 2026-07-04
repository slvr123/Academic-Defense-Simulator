"""PDF chunking helpers."""

from __future__ import annotations

from pathlib import Path

_TARGET_CHARS = 2800   # ~700 tokens * 4 chars/token (midpoint of 600-800 range)
_OVERLAP_CHARS = 400   # ~100 tokens * 4 chars/token


def chunk_pdf(path: str | Path) -> list[str]:
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:
        raise RuntimeError("PyMuPDF is required for PDF chunking") from exc

    document = fitz.open(str(path))
    paragraphs: list[str] = []
    for page in document:
        text = page.get_text("text")
        paragraphs.extend(
            p.strip() for p in text.split("\n\n") if p.strip()
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
