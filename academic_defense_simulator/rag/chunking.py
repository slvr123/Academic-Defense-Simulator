"""PDF chunking helpers."""

from __future__ import annotations

from pathlib import Path


def chunk_pdf(path: str | Path) -> list[str]:
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:  # pragma: no cover - optional dependency.
        raise RuntimeError("PyMuPDF is required for PDF chunking") from exc

    document = fitz.open(str(path))
    chunks: list[str] = []
    for page in document:
        text = page.get_text("text")
        paragraphs = [paragraph.strip() for paragraph in text.split("\n\n") if paragraph.strip()]
        chunks.extend(paragraphs)
    return chunks
