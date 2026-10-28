"""M7 J2/J3 through the API: ingest tags literature-review chunks, and with
ADS_ATTRIBUTION_NOTES on, only the question prompts see the note. The source
reveal, and everything with the flag off, stays as before."""

from __future__ import annotations

import json

import pytest

from backend.engine_adapter import ATTRIBUTION_NOTES
from backend.page_filter import chunk_pdf_filtered
from backend.settings import Settings
from backend.tests.conftest import THESIS_WITH_REVIEW, make_page_pdf, upload, wait_for

NOTE_START = "[Note to the panelist:"
# Three of the fixture's four chunks are tagged, and since M9 L2 only the Literature
# Specialist may draw them (with lit_review_access="literature"), so it carries the notes.
PANEL = json.dumps({"archetype_keys": ["methodology_expert", "literature_theory_specialist"]})


@pytest.fixture
def review_pdf(tmp_path):
    return make_page_pdf(tmp_path / "Kiosk Thesis.pdf", THESIS_WITH_REVIEW)


def _ready(client, pdf) -> str:
    session_id = upload(client, pdf, panel=PANEL).json()["session_id"]
    wait_for(client, session_id, lambda s: s.get("status") == "ready")
    return session_id


def _run_defense(client, session_id) -> list[dict]:
    """Every question's source, in order, from /start to the report."""
    start = client.post(f"/sessions/{session_id}/start")
    assert start.status_code == 200
    sources = [start.json()["question"]["sources"][0]]
    while True:
        body = client.post(f"/sessions/{session_id}/answers", json={"text": "We used thirty respondents."}).json()
        if body["done"]:
            return sources
        sources.append(body["next_question"]["sources"][0])


def _attribution_of(metas, source: dict):
    [meta] = [m for m in metas if m.text == source["text"]]
    return meta.attribution


def test_the_flag_is_off_by_default():
    assert Settings(_env_file=None).attribution_notes is False


def test_ingest_stores_each_chunks_tag(client_factory, review_pdf):
    with client_factory() as client:
        session_id = _ready(client, review_pdf)
        metas = client.app.state.store.get(session_id).chunks
    expected = chunk_pdf_filtered(review_pdf).attributions
    assert [m.attribution for m in metas] == expected
    assert expected == ["partly_literature_review", "literature_review", "partly_literature_review", None]


def test_with_the_flag_off_no_prompt_carries_a_note(client_factory, gemini, review_pdf):
    with client_factory(lit_review_access="literature") as client:
        session_id = _ready(client, review_pdf)
        sources = _run_defense(client, session_id)
        session = client.app.state.store.get(session_id)
        defense_chunks, metas = session.defense.chunks, session.chunks
    assert all(c.text is m.text for c, m in zip(defense_chunks, metas))
    assert len(gemini.question_prompts) == len(sources) >= 3
    assert not any(NOTE_START in prompt for prompt in gemini.question_prompts)


def test_with_the_flag_on_tagged_chunks_reach_the_prompt_with_their_note(client_factory, gemini, review_pdf):
    with client_factory(attribution_notes=True, lit_review_access="literature") as client:
        session_id = _ready(client, review_pdf)
        sources = _run_defense(client, session_id)
        metas = client.app.state.store.get(session_id).chunks
    assert len(gemini.question_prompts) == len(sources) >= 3
    for prompt, source in zip(gemini.question_prompts, sources):
        attribution = _attribution_of(metas, source)
        if attribution is None:
            assert NOTE_START not in prompt
        else:
            assert ATTRIBUTION_NOTES[attribution] in prompt
        assert NOTE_START not in source["text"]  # the source reveal shows the document's own text
    assert any(_attribution_of(metas, s) is not None for s in sources)  # a note was exercised
