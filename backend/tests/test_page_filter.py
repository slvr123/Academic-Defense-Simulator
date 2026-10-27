"""v2.0 M5 and M6 H1: navigation pages and the reference list are left out of chunking;
M7 J2: literature-review chunks are tagged (backend/page_filter.py)."""

from __future__ import annotations

from pathlib import Path

import fitz  # PyMuPDF
import pytest

from academic_defense_simulator.rag.chunking import InsufficientTextError, _paragraphs_from_page_text, chunk_pdf_with_pages
from backend.page_filter import (
    LiteratureReviewRange,
    ReferenceRange,
    _paragraph_lines,
    chunk_pdf_filtered,
    is_navigation_page,
    literature_review_range,
    reference_range,
    removable_pages,
    trim_references,
)
from backend.tests.conftest import (
    APPENDIX_MARKER,
    CHAPTER_FIVE_MARKER,
    CITED_MARKER,
    CONTENTS_MARKER,
    OWN_MARKER,
    REFERENCE_MARKER,
    THESIS_WITH_REVIEW,
    cited_text,
    make_navigation_pdf,
    make_page_pdf,
    make_pdf,
    own_text,
)

SAMPLE_PDF = Path(__file__).resolve().parents[2] / "academic_defense_simulator" / "sample" / "sample-capstone-anicheck.pdf"
FULL = ["title", "contents", "abstract", "chapter_one", "results"]


def _page_texts(path: Path) -> list[str]:
    with fitz.open(str(path)) as document:
        return [page.get_text("text") for page in document]


def _as_tuples(chunks) -> list[tuple[str, int, int]]:
    return [(c.text, c.page_start, c.page_end) for c in chunks]


# --- parity with v1 ---------------------------------------------------------


def test_with_nothing_to_remove_the_chunks_are_v1s(tmp_path):
    path = make_pdf(tmp_path / "plain.pdf", pages=6)
    filtered = chunk_pdf_filtered(path)
    assert (filtered.removed_pages, filtered.references) == ([], None)
    assert _as_tuples(filtered.chunks) == _as_tuples(chunk_pdf_with_pages(path))


def test_on_the_bundled_sample_only_the_reference_page_differs_from_v1():
    # No navigation pages; REFERENCES heads its last page (16 of 16), so M6 trims it.
    v1 = chunk_pdf_with_pages(SAMPLE_PDF)
    assert _as_tuples(chunk_pdf_filtered(SAMPLE_PDF, trim_reference_list=False).chunks) == _as_tuples(v1)
    filtered = chunk_pdf_filtered(SAMPLE_PDF)
    assert filtered.references is not None and filtered.references.start[0] == 15
    assert _as_tuples(filtered.chunks) == _as_tuples(v1)[:-1]  # the one chunk on page 16 is gone


# --- what gets removed --------------------------------------------------------


def test_only_the_contents_page_is_removed(tmp_path):
    path = make_navigation_pdf(tmp_path / "thesis.pdf", FULL)
    texts = _page_texts(path)
    # The fixture is what it claims: the contents and results pages both look like navigation.
    assert [is_navigation_page(t) for t in texts] == [False, True, False, False, True]

    filtered = chunk_pdf_filtered(path)
    assert filtered.removed_pages == [1]
    joined = "\n".join(c.text for c in filtered.chunks)
    assert CONTENTS_MARKER not in joined
    assert "Abstract" in joined and "CHAPTER 1" in joined and "Portability 22" in joined


def test_pages_at_or_after_the_boundary_are_never_removed(tmp_path):
    path = make_navigation_pdf(tmp_path / "thesis.pdf", ["title", "chapter_one", "contents", "results"])
    assert removable_pages(_page_texts(path)) == set()


def test_no_boundary_means_nothing_is_removed(tmp_path):
    path = make_navigation_pdf(tmp_path / "report.pdf", ["title", "contents", "abstract", "results"])
    filtered = chunk_pdf_filtered(path)
    assert filtered.removed_pages == []
    assert _as_tuples(filtered.chunks) == _as_tuples(chunk_pdf_with_pages(path))


# --- page numbers and the scanned check ----------------------------------------


def test_page_numbers_refer_to_the_original_pages(tmp_path):
    # Contents is page 0, before the boundary (Chapter 1, page 2), so it goes; what's
    # left starts on page 1, the abstract, and isn't renumbered to start at 0.
    path = make_navigation_pdf(tmp_path / "thesis.pdf", ["contents", "abstract", "chapter_one", "results"])
    filtered = chunk_pdf_filtered(path)
    assert filtered.removed_pages == [0]
    assert [(c.page_start, c.page_end) for c in filtered.chunks] == [(1, 3)]
    assert filtered.chunks[0].text.startswith("Abstract")


def test_the_scanned_check_counts_removed_pages_too(tmp_path):
    # Without the contents page, the remaining text is under v1's 500-character minimum,
    # so a check counting only kept pages would call this document scanned.
    path = make_navigation_pdf(tmp_path / "short.pdf", ["contents", "contents", "chapter_one"])
    texts = _page_texts(path)
    assert sum(len(t) for t in texts) >= 500 > len(texts[2])
    filtered = chunk_pdf_filtered(path)
    assert filtered.removed_pages == [0, 1]
    assert filtered.chunks and CONTENTS_MARKER not in filtered.chunks[0].text


def test_a_blank_pdf_is_still_insufficient_text_with_v1s_count(tmp_path):
    path = make_pdf(tmp_path / "blank.pdf", text=False)
    with pytest.raises(InsufficientTextError) as v1:
        chunk_pdf_with_pages(path)
    with pytest.raises(InsufficientTextError) as filtered:
        chunk_pdf_filtered(path)
    assert filtered.value.total_chars == v1.value.total_chars


# --- M6 H1: the reference list ------------------------------------------------

THESIS_WITH_REFERENCES = ["title", "chapter_one", "body", "body", "chapter_five_then_references", "references", "appendix"]


def test_the_reference_list_goes_and_chapter_five_and_the_appendix_stay(tmp_path):
    path = make_navigation_pdf(tmp_path / "thesis.pdf", THESIS_WITH_REFERENCES)
    filtered = chunk_pdf_filtered(path)
    # REFERENCES is line 2 of page 4; the appendix heading is line 0 of page 6.
    assert filtered.references == ReferenceRange(start=(4, 2), end=(6, 0))
    joined = "\n".join(c.text for c in filtered.chunks)
    assert REFERENCE_MARKER not in joined and "REFERENCES" not in joined
    assert CHAPTER_FIVE_MARKER in joined  # above the heading on its page
    assert APPENDIX_MARKER in joined and "APPENDIX A - Survey Instrument" in joined
    # Page numbers are the PDF's own: the last chunk still ends on the appendix page.
    assert filtered.chunks[-1].page_end == 6


def test_with_no_end_heading_the_list_runs_to_the_end_of_the_document(tmp_path):
    path = make_navigation_pdf(tmp_path / "thesis.pdf", THESIS_WITH_REFERENCES[:-1])
    filtered = chunk_pdf_filtered(path)
    assert filtered.references == ReferenceRange(start=(4, 2), end=None)
    joined = "\n".join(c.text for c in filtered.chunks)
    assert REFERENCE_MARKER not in joined and CHAPTER_FIVE_MARKER in joined
    assert filtered.chunks[-1].page_end == 4


def test_an_early_literature_cited_chapter_turns_the_filter_off(tmp_path):
    # The first references heading after the boundary is in the first half, so the rule
    # filters nothing at all, even though a real reference list follows (fail-open).
    pages = ["title", "chapter_one", "early_literature_cited", "body", "body", "chapter_five_then_references", "references"]
    path = make_navigation_pdf(tmp_path / "thesis.pdf", pages)
    filtered = chunk_pdf_filtered(path)
    assert filtered.references is None
    assert _as_tuples(filtered.chunks) == _as_tuples(chunk_pdf_with_pages(path))


def test_no_references_heading_means_nothing_is_removed(tmp_path):
    path = make_navigation_pdf(tmp_path / "thesis.pdf", ["title", "chapter_one", "body", "body", "appendix"])
    filtered = chunk_pdf_filtered(path)
    assert filtered.references is None
    assert _as_tuples(filtered.chunks) == _as_tuples(chunk_pdf_with_pages(path))


def test_the_m5_chunker_is_still_available_for_the_eval(tmp_path):
    path = make_navigation_pdf(tmp_path / "thesis.pdf", THESIS_WITH_REFERENCES)
    m5 = chunk_pdf_filtered(path, trim_reference_list=False)
    assert m5.references is None
    assert _as_tuples(m5.chunks) == _as_tuples(chunk_pdf_with_pages(path))  # no navigation pages here


def test_the_scanned_check_counts_the_trimmed_reference_text(tmp_path, monkeypatch):
    path = make_navigation_pdf(tmp_path / "thesis.pdf", THESIS_WITH_REFERENCES)
    texts = _page_texts(path)
    total = sum(len(t) for t in texts)
    kept = sum(len(t) for t in trim_references(texts, reference_range(texts)))
    assert kept < total
    # A minimum between what's kept and what's there: only a check over all text passes.
    monkeypatch.setattr("backend.page_filter._MIN_EXTRACTED_CHARS", (kept + total) // 2)
    assert chunk_pdf_filtered(path).chunks


@pytest.mark.parametrize(
    "line",
    ["References", "REFERENCES", "Bibliography", "Literature Cited", "works cited", "6. References", "VI. BIBLIOGRAPHY", "References:"],
)
def test_references_headings(line):
    pages = ["CHAPTER 1\nIntroduction", "Body", f"Body\n{line}\nAbad, J. (2019)."]
    assert reference_range(pages) == ReferenceRange(start=(2, 1), end=None)


@pytest.mark.parametrize(
    "line",
    ["References .................... 80", "References 80", "List of References", "References and notes", "Review of Related Literature"],
)
def test_not_references_headings(line):
    assert reference_range(["CHAPTER 1\nIntroduction", "Body", f"Body\n{line}\nAbad, J. (2019)."]) is None


def test_a_references_heading_before_the_boundary_is_ignored():
    # A contents-free front matter that names its references: before Chapter 1, so not a start.
    pages = ["References", "CHAPTER 1\nIntroduction", "Body", "Body"]
    assert reference_range(pages) is None


@pytest.mark.parametrize(
    ("line", "ends"),
    [
        ("Appendix A", True),
        ("APPENDIX 1", True),
        ("Appendices", True),
        ("Appendix B – Letter of Endorsement", True),
        ("Appendix C: Interview Transcript", True),
        ("Curriculum Vitae", True),
        ("GLOSSARY", True),
        ("Appendix A – Survey Instrument ........ 85", False),
        ("Appendix A – Survey Instrument 85", False),
        ("Appendix tables were adapted from the 2019 survey.", False),
        ("Cruz, M. (2020). Glossary of terms.", False),
    ],
)
def test_end_headings(line, ends):
    pages = ["CHAPTER 1\nIntroduction", "Body", f"References\nAbad, J. (2019).\n{line}\nMore"]
    end = (2, 2) if ends else None
    assert reference_range(pages) == ReferenceRange(start=(2, 0), end=end)


def test_trim_keeps_text_above_the_start_and_from_the_end_heading_on():
    pages = ["Intro", "Ch5 last line\nReferences\nRef one\n", "Ref two\nAppendix A\nAppendix body\n"]
    trimmed = trim_references(pages, ReferenceRange(start=(1, 1), end=(2, 1)))
    assert trimmed == ["Intro", "Ch5 last line\n", "Appendix A\nAppendix body\n"]


def test_trim_on_a_single_page():
    pages = ["Ch5\nReferences\nRef\nGlossary\nTerm"]
    assert trim_references(pages, ReferenceRange(start=(0, 1), end=(0, 3))) == ["Ch5\nGlossary\nTerm"]


# --- M7 J2: the literature review is tagged, never changed ----------------------


def test_chunks_in_the_review_are_tagged_and_straddling_ones_are_partly(tmp_path):
    path = make_page_pdf(tmp_path / "thesis.pdf", THESIS_WITH_REVIEW)
    filtered = chunk_pdf_filtered(path)
    assert filtered.literature_review == LiteratureReviewRange(start=(2, 0), end=(6, 0))
    assert [(c.page_start, c.page_end) for c in filtered.chunks] == [(0, 2), (3, 4), (5, 6), (7, 7)]
    assert filtered.attributions == [
        "partly_literature_review",  # Chapter 1, then the review's first page
        "literature_review",
        "partly_literature_review",  # the review's last page, then Chapter 3
        None,
    ]
    texts = [c.text for c in filtered.chunks]
    assert [(OWN_MARKER in t, CITED_MARKER in t) for t in texts] == [(True, True), (False, True), (True, True), (True, False)]


def test_tagging_never_changes_a_chunk(tmp_path):
    path = make_page_pdf(tmp_path / "thesis.pdf", THESIS_WITH_REVIEW)
    assert _as_tuples(chunk_pdf_filtered(path).chunks) == _as_tuples(chunk_pdf_with_pages(path))


def test_no_literature_review_heading_means_no_tags(tmp_path):
    pages = ["CHAPTER 1: INTRODUCTION\n" + own_text(1), cited_text(1), cited_text(2), "CHAPTER 3\n" + own_text(2)]
    filtered = chunk_pdf_filtered(make_page_pdf(tmp_path / "thesis.pdf", pages))
    assert filtered.literature_review is None
    assert filtered.attributions == [None] * len(filtered.chunks)


def test_a_start_with_no_end_tags_nothing(tmp_path):
    filtered = chunk_pdf_filtered(make_page_pdf(tmp_path / "thesis.pdf", THESIS_WITH_REVIEW[:6]))
    assert filtered.literature_review is None
    assert filtered.attributions == [None] * len(filtered.chunks)


def test_no_front_matter_boundary_means_no_tags(tmp_path):
    pages = ["CHAPTER 2: REVIEW OF RELATED LITERATURE\n" + cited_text(1), "CHAPTER 3\n" + own_text(1)]
    filtered = chunk_pdf_filtered(make_page_pdf(tmp_path / "report.pdf", pages))
    assert filtered.literature_review is None and set(filtered.attributions) == {None}


def test_the_bundled_sample_tags_its_chapter_two():
    filtered = chunk_pdf_filtered(SAMPLE_PDF)
    # Each page is one paragraph that opens with its page number, above any heading:
    # CHAPTER 2 is line 1 of page 6 (0-based 5) and CHAPTER 3 line 1 of page 8. A
    # paragraph belongs where it ends, so pages 6 and 7 are tagged and page 8 isn't.
    assert filtered.literature_review == LiteratureReviewRange(start=(5, 1), end=(7, 1))
    tagged = [(c.page_start, c.page_end, a) for c, a in zip(filtered.chunks, filtered.attributions) if a]
    assert tagged == [(5, 5, "literature_review"), (6, 6, "literature_review")]


def _review_pages(start_line: str, end_line: str = "CHAPTER 3") -> list[str]:
    return ["CHAPTER 1\nIntroduction", f"Our own background.\n{start_line}\nCited work.", f"More cited work.\n{end_line}\nOur design."]


@pytest.mark.parametrize(
    "line",
    [
        "CHAPTER 2", "Chapter II", "CHAPTER TWO", "Chapter 2.", "CHAPTER 2: REVIEW OF RELATED LITERATURE",
        "Chapter 2 - Review of Related Literature", "CHAPTER II REVIEW OF RELATED LITERATURE AND STUDIES",
        "Review of Related Literature", "LITERATURE REVIEW", "2.1 Related Works", "Related Literature:",
    ],
)
def test_literature_review_start_headings(line):
    assert literature_review_range(_review_pages(line)) == LiteratureReviewRange(start=(1, 1), end=(2, 1))


@pytest.mark.parametrize(
    "line",
    [
        "Chapter 2 presents the review of related literature",  # a sentence, not a heading
        "CHAPTER 2 REVIEW OF RELATED LITERATURE ........ 13",
        "CHAPTER 2 REVIEW OF RELATED LITERATURE 13",
        "Chapter 20", "Chapter III", "Review of Related Literature and Studies", "A literature review of kiosks",
    ],
)
def test_not_literature_review_start_headings(line):
    assert literature_review_range(_review_pages(line)) is None


@pytest.mark.parametrize("line", ["CHAPTER 3", "Chapter III", "CHAPTER 3: METHODOLOGY", "Methodology", "3. METHODOLOGY"])
def test_literature_review_end_headings(line):
    assert literature_review_range(_review_pages("CHAPTER 2", line)) == LiteratureReviewRange(start=(1, 1), end=(2, 1))


@pytest.mark.parametrize(
    "line", ["Chapter 3 describes the methodology", "methodology that emphasizes iterative development", "Chapter 30"]
)
def test_not_literature_review_end_headings(line):
    assert literature_review_range(_review_pages("CHAPTER 2", line)) is None


def test_a_chapter_two_line_in_the_contents_is_not_the_start():
    pages = ["Contents\nCHAPTER 2\nCHAPTER 3", "CHAPTER 1\nIntroduction", "CHAPTER 2\nCited work.", "CHAPTER 3\nOurs."]
    assert literature_review_range(pages) == LiteratureReviewRange(start=(2, 0), end=(3, 0))


def test_paragraph_lines_follow_v1s_paragraphs():
    # A blank line carrying a space still splits, and a lone page number is dropped (v1).
    text = "First line\nstill first\n \nSecond\n\n12\n\nThird"
    paragraphs = _paragraphs_from_page_text(text)
    assert paragraphs == ["First line\nstill first", "Second", "Third"]
    assert _paragraph_lines(text, paragraphs) == [1, 3, 7]  # the line each one ends on
