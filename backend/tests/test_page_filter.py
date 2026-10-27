"""v2.0 M5: front-matter navigation pages are left out of chunking (backend/page_filter.py)."""

from __future__ import annotations

from pathlib import Path

import fitz  # PyMuPDF
import pytest

from academic_defense_simulator.rag.chunking import InsufficientTextError, chunk_pdf_with_pages
from backend.page_filter import chunk_pdf_without_navigation, is_navigation_page, removable_pages
from backend.tests.conftest import CONTENTS_MARKER, make_navigation_pdf, make_pdf

SAMPLE_PDF = Path(__file__).resolve().parents[2] / "academic_defense_simulator" / "sample" / "sample-capstone-anicheck.pdf"
FULL = ["title", "contents", "abstract", "chapter_one", "results"]


def _page_texts(path: Path) -> list[str]:
    with fitz.open(str(path)) as document:
        return [page.get_text("text") for page in document]


def _as_tuples(chunks) -> list[tuple[str, int, int]]:
    return [(c.text, c.page_start, c.page_end) for c in chunks]


# --- parity with v1 ---------------------------------------------------------


@pytest.mark.parametrize("source", ["synthetic", "sample"])
def test_without_navigation_pages_the_chunks_are_v1s(tmp_path, source):
    path = make_pdf(tmp_path / "plain.pdf", pages=6) if source == "synthetic" else SAMPLE_PDF
    filtered = chunk_pdf_without_navigation(path)
    assert filtered.removed_pages == []
    assert _as_tuples(filtered.chunks) == _as_tuples(chunk_pdf_with_pages(path))


# --- what gets removed --------------------------------------------------------


def test_only_the_contents_page_is_removed(tmp_path):
    path = make_navigation_pdf(tmp_path / "thesis.pdf", FULL)
    texts = _page_texts(path)
    # The fixture is what it claims: the contents and results pages both look like navigation.
    assert [is_navigation_page(t) for t in texts] == [False, True, False, False, True]

    filtered = chunk_pdf_without_navigation(path)
    assert filtered.removed_pages == [1]
    joined = "\n".join(c.text for c in filtered.chunks)
    assert CONTENTS_MARKER not in joined
    assert "Abstract" in joined and "CHAPTER 1" in joined and "Portability 22" in joined


def test_pages_at_or_after_the_boundary_are_never_removed(tmp_path):
    path = make_navigation_pdf(tmp_path / "thesis.pdf", ["title", "chapter_one", "contents", "results"])
    assert removable_pages(_page_texts(path)) == set()


def test_no_boundary_means_nothing_is_removed(tmp_path):
    path = make_navigation_pdf(tmp_path / "report.pdf", ["title", "contents", "abstract", "results"])
    filtered = chunk_pdf_without_navigation(path)
    assert filtered.removed_pages == []
    assert _as_tuples(filtered.chunks) == _as_tuples(chunk_pdf_with_pages(path))


# --- page numbers and the scanned check ----------------------------------------


def test_page_numbers_refer_to_the_original_pages(tmp_path):
    # Contents is page 0, before the boundary (Chapter 1, page 2), so it goes; what's
    # left starts on page 1, the abstract, and isn't renumbered to start at 0.
    path = make_navigation_pdf(tmp_path / "thesis.pdf", ["contents", "abstract", "chapter_one", "results"])
    filtered = chunk_pdf_without_navigation(path)
    assert filtered.removed_pages == [0]
    assert [(c.page_start, c.page_end) for c in filtered.chunks] == [(1, 3)]
    assert filtered.chunks[0].text.startswith("Abstract")


def test_the_scanned_check_counts_removed_pages_too(tmp_path):
    # Without the contents page, the remaining text is under v1's 500-character minimum,
    # so a check counting only kept pages would call this document scanned.
    path = make_navigation_pdf(tmp_path / "short.pdf", ["contents", "contents", "chapter_one"])
    texts = _page_texts(path)
    assert sum(len(t) for t in texts) >= 500 > len(texts[2])
    filtered = chunk_pdf_without_navigation(path)
    assert filtered.removed_pages == [0, 1]
    assert filtered.chunks and CONTENTS_MARKER not in filtered.chunks[0].text


def test_a_blank_pdf_is_still_insufficient_text_with_v1s_count(tmp_path):
    path = make_pdf(tmp_path / "blank.pdf", text=False)
    with pytest.raises(InsufficientTextError) as v1:
        chunk_pdf_with_pages(path)
    with pytest.raises(InsufficientTextError) as filtered:
        chunk_pdf_without_navigation(path)
    assert filtered.value.total_chars == v1.value.total_chars
