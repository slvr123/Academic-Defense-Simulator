"""Chunking tests (Task 3, item 2): paragraph-aware packing + overlap.

Exercises the pure packer `_pack` directly with synthetic paragraph lists — no PDF, no
`fitz`. `chunk_pdf` only splits page text into paragraphs and delegates here.
The v2.0 M1 tests at the end do open PDFs: page ranges and the insufficient-text error.
"""

from __future__ import annotations

import json
from pathlib import Path

import fitz  # PyMuPDF
import pytest

from academic_defense_simulator.rag.chunking import (
    OVERLAP_CHARS,
    TARGET_CHARS,
    DocumentIngestionError,
    InsufficientTextError,
    _pack,
    _paragraphs_from_page_text,
    chunk_pdf,
    chunk_pdf_with_pages,
)


def _paras(count, size):
    # Uniquely identifiable paragraphs so membership checks (overlap, boundary) are exact.
    return [f"p{i:03d}-" + "x" * size for i in range(count)]


def test_paragraphs_under_target_stay_one_chunk():
    paras = _paras(3, 200)  # 3 * ~205 chars, well under 2800
    chunks = _pack(paras)
    assert len(chunks) == 1
    assert chunks[0] == "\n\n".join(paras)


def test_large_input_splits_into_multiple_chunks():
    chunks = _pack(_paras(40, 150))  # ~6000 chars total
    assert len(chunks) >= 2


def test_no_chunk_exceeds_target_when_paragraphs_are_small():
    # The packer budgets against paragraph *content* chars (its token-count proxy), so the
    # invariant is on the sum of paragraph lengths, not the rendered string — the latter is
    # a little longer because of the "\n\n" separators it joins on.
    for chunk in _pack(_paras(40, 150)):
        content_chars = sum(len(p) for p in chunk.split("\n\n"))
        assert content_chars <= TARGET_CHARS


def test_paragraph_boundaries_are_respected():
    paras = _paras(40, 150)
    original = set(paras)
    for chunk in _pack(paras):
        for piece in chunk.split("\n\n"):
            assert piece in original, "a chunk contained a fragment that was not a whole paragraph"


def test_consecutive_chunks_overlap_within_bound():
    paras = _paras(40, 150)  # 150 < 400, so whole paragraphs get carried back as overlap
    chunks = _pack(paras)
    first = chunks[0].split("\n\n")
    second = chunks[1].split("\n\n")

    shared = [p for p in second if p in set(first)]
    assert shared, "expected paragraph overlap between consecutive chunks"
    # Overlap is the head of chunk 2 and the tail of chunk 1, and stays within the budget.
    assert second[: len(shared)] == shared
    assert first[-len(shared):] == shared
    assert sum(len(p) for p in shared) <= OVERLAP_CHARS


def test_oversized_paragraph_is_not_split():
    big = "BIG-" + "y" * (TARGET_CHARS * 2)  # single paragraph larger than the target
    chunks = _pack([_paras(1, 150)[0], big, _paras(1, 150)[0]])
    assert any(big in chunk for chunk in chunks), "oversized paragraph must survive intact"


def test_empty_input_yields_no_chunks():
    assert _pack([]) == []


# --- v0.3 hardening, Miss 3 (Decision 8): page-number-paragraph stripping ---
# Fixtures below reproduce the real DAZSMA corruption pattern confirmed via a fresh
# extraction: a scanned/image-only appendix run leaving nothing behind but consecutive
# standalone page-number paragraphs, glued to real text by a stray-whitespace "blank" line.


def test_standalone_page_number_paragraphs_are_dropped():
    # "fail" fixture: the real corrupted shape -- a run of pure page-number paragraphs
    # sitting between two real headings, exactly as PyMuPDF extracted it from DAZSMA.
    page_text = (
        "detection of misplaced or missing books.\n \n174\n\nAppendices \n"
        "Appendix A – Approved Concept Proposal Paper \n \n \n175\n\n176\n\n177\n\n178\n\n179\n\n"
        "Appendix B – Letter of Endorsement \n \n180"
    )
    paragraphs = _paragraphs_from_page_text(page_text)

    assert paragraphs == [
        "detection of misplaced or missing books.",
        "Appendices \nAppendix A – Approved Concept Proposal Paper",
        "Appendix B – Letter of Endorsement",
    ]
    joined = " ".join(paragraphs)
    for stray in ("174", "175", "176", "177", "178", "179", "180"):
        assert stray not in joined


def test_table_cell_numbers_within_a_paragraph_are_preserved():
    # "pass" fixture: a real table quantity/price column -- lines *within* one larger
    # paragraph, never their own paragraph, per the actual DAZSMA cost-table layout.
    page_text = (
        "Casing \nKeytech T-100 \nTempered Glass Micro ATX Gaming PC Case Black \n"
        "1 \n₱1,047.00\n\n₱1,047.00\n\nMonitor \nNvision N200V8 20\" LED Monitor \n"
        "1 \n₱1,810.00"
    )
    paragraphs = _paragraphs_from_page_text(page_text)

    assert any("Keytech T-100" in p and "1 " in p for p in paragraphs)
    assert any("Nvision N200V8" in p and "1,810.00" in p for p in paragraphs)
    # the lone "1" quantity must never be filtered out as if it were a page number
    assert not any(p == "1" for p in paragraphs)


def test_real_page_number_in_running_prose_is_untouched():
    # A number that happens to look page-number-like but is embedded in a real sentence
    # (not its own paragraph) must survive -- the filter only ever matches a whole paragraph.
    page_text = "The evaluation involved 20 respondents drawn from three departments."
    assert _paragraphs_from_page_text(page_text) == [
        "The evaluation involved 20 respondents drawn from three departments."
    ]


# --- v2.0 M1: page ranges and the insufficient-text subclass ---

SAMPLE_DIR = Path(__file__).resolve().parent.parent / "academic_defense_simulator" / "sample"


def test_page_chunks_match_the_committed_sample_chunks():
    # The sidecar's chunks.json was written by chunk_pdf, so this pins that adding page
    # tracking left the chunk text byte-for-byte unchanged.
    committed = json.loads((SAMPLE_DIR / "chunks.json").read_text(encoding="utf-8"))
    page_chunks = chunk_pdf_with_pages(SAMPLE_DIR / "sample-capstone-anicheck.pdf")
    assert [c.text for c in page_chunks] == committed
    assert chunk_pdf(SAMPLE_DIR / "sample-capstone-anicheck.pdf") == committed


def test_page_ranges_are_ordered_and_within_the_document():
    page_chunks = chunk_pdf_with_pages(SAMPLE_DIR / "sample-capstone-anicheck.pdf")
    assert page_chunks[0].page_start == 0
    assert page_chunks[-1].page_end == 15  # 16-page sample, 0-based
    for chunk in page_chunks:
        assert 0 <= chunk.page_start <= chunk.page_end <= 15
    starts = [c.page_start for c in page_chunks]
    assert starts == sorted(starts)


def test_page_range_comes_from_page_indices_not_text(tmp_path):
    # Two pages whose text contains a misleading number; the range must be 0..1 anyway.
    document = fitz.open()
    for body in ("Page 42 of a report. " * 20, "Chapter 7 continues here. " * 20):
        document.new_page().insert_textbox(fitz.Rect(36, 36, 576, 756), body)
    path = tmp_path / "two_pages.pdf"
    document.save(path)

    [chunk] = chunk_pdf_with_pages(path)
    assert (chunk.page_start, chunk.page_end) == (0, 1)


def test_blank_pdf_raises_insufficient_text_error_a_document_ingestion_error(tmp_path):
    document = fitz.open()
    document.new_page()
    path = tmp_path / "blank.pdf"
    document.save(path)

    with pytest.raises(InsufficientTextError) as caught:
        chunk_pdf_with_pages(path)
    assert isinstance(caught.value, DocumentIngestionError)
    assert caught.value.total_chars == 0
