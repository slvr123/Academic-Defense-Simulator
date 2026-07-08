"""Chunking tests (Task 3, item 2): paragraph-aware packing + overlap.

Exercises the pure packer `_pack` directly with synthetic paragraph lists — no PDF, no
`fitz`. `chunk_pdf` only splits page text into paragraphs and delegates here.
"""

from __future__ import annotations

from academic_defense_simulator.rag.chunking import _OVERLAP_CHARS, _TARGET_CHARS, _pack


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
        assert content_chars <= _TARGET_CHARS


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
    assert sum(len(p) for p in shared) <= _OVERLAP_CHARS


def test_oversized_paragraph_is_not_split():
    big = "BIG-" + "y" * (_TARGET_CHARS * 2)  # single paragraph larger than the target
    chunks = _pack([_paras(1, 150)[0], big, _paras(1, 150)[0]])
    assert any(big in chunk for chunk in chunks), "oversized paragraph must survive intact"


def test_empty_input_yields_no_chunks():
    assert _pack([]) == []
