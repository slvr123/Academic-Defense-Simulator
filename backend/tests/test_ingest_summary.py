"""M10 N6: the ingest summary on GET /sessions/{id}: page count, passages the panel can
cite, and the pages the M5/M6 filters left out, all from ingest's own positions."""

from __future__ import annotations

import json

from backend.ingest_summary import _runs, skipped_pages
from backend.page_filter import chunk_pdf_filtered
from backend.schemas import PageRange
from backend.tests.conftest import THESIS_WITH_REVIEW, make_navigation_pdf, make_page_pdf, make_pdf, upload, wait_for

# Contents at page 2; REFERENCES on line 2 of page 6 (1-based) up to the appendix heading, line 0 of page 8.
THESIS = ["title", "contents", "chapter_one", "body", "body", "chapter_five_then_references", "references", "appendix"]


def _ready(body: dict) -> bool:
    return body.get("status") == "ready"


def test_consecutive_pages_collapse_into_runs():
    assert _runs([]) == []
    assert _runs([1, 2, 3, 6, 8, 9]) == [PageRange(start=2, end=4), PageRange(start=7, end=7), PageRange(start=9, end=10)]


def test_the_reference_list_ends_before_the_page_its_end_heading_opens(tmp_path):
    path = make_navigation_pdf(tmp_path / "thesis.pdf", THESIS)
    skipped = skipped_pages(chunk_pdf_filtered(path), page_count=len(THESIS))
    assert skipped.navigation == [PageRange(start=2, end=2)]
    assert skipped.references == PageRange(start=6, end=7)


def test_with_no_end_heading_the_reference_list_runs_to_the_last_page(tmp_path):
    path = make_navigation_pdf(tmp_path / "thesis.pdf", THESIS[:-1])
    assert skipped_pages(chunk_pdf_filtered(path), page_count=len(THESIS) - 1).references == PageRange(start=6, end=7)


def test_a_ready_session_reports_what_ingest_read_and_left_out(client_factory, tmp_path):
    path = make_navigation_pdf(tmp_path / "thesis.pdf", THESIS)
    with client_factory() as client:
        session_id = upload(client, path).json()["session_id"]
        state = wait_for(client, session_id, _ready)
    assert state["ingest"] == {
        "page_count": 8,
        "citable_passages": state["chunk_count"],
        "navigation_pages": [{"start": 2, "end": 2}],
        "reference_list": {"start": 6, "end": 7},
        "held_back_passages": 0,
    }  # the route drops None fields, so no literature_review


def test_under_none_the_review_passages_are_held_back(client_factory, tmp_path):
    # Chapter 2 runs pages 3-6; the CHAPTER 3 heading opens page 7.
    path = make_page_pdf(tmp_path / "Kiosk Thesis.pdf", THESIS_WITH_REVIEW)
    with client_factory(lit_review_access="none") as client:
        session_id = upload(client, path).json()["session_id"]
        state = wait_for(client, session_id, _ready)
        tagged = sum(c.attribution is not None for c in client.app.state.store.get(session_id).chunks)
    summary = state["ingest"]
    assert tagged > 0
    assert summary["held_back_passages"] == tagged
    assert summary["citable_passages"] == state["chunk_count"] - tagged
    assert summary["literature_review"] == {"start": 3, "end": 6}


def test_a_panel_with_the_literature_specialist_can_cite_every_passage(client_factory, tmp_path):
    path = make_page_pdf(tmp_path / "Kiosk Thesis.pdf", THESIS_WITH_REVIEW)
    panel = json.dumps({"archetype_keys": ["literature_theory_specialist"]})
    with client_factory(lit_review_access="literature") as client:
        session_id = upload(client, path, panel=panel).json()["session_id"]
        state = wait_for(client, session_id, _ready)
    assert state["ingest"]["citable_passages"] == state["chunk_count"]
    assert state["ingest"]["held_back_passages"] == 0 and "literature_review" not in state["ingest"]


def test_the_summary_is_absent_until_ready_and_on_failure(client_factory, tmp_path, embedder):
    scanned = make_pdf(tmp_path / "scanned.pdf", pages=2, text=False)
    with client_factory() as client:
        failed = wait_for(client, upload(client, scanned).json()["session_id"], lambda b: b.get("status") == "failed")
        embedder.hold()
        session_id = upload(client, make_pdf(tmp_path / "doc.pdf")).json()["session_id"]
        embedding = wait_for(client, session_id, lambda b: b.get("status") == "embedding")
        embedder.release()
    assert "ingest" not in failed and "ingest" not in embedding
