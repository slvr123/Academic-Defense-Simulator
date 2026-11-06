"""M16: the panel's language. The flag (Decision 6), the threading into every question
path and the closing lines, and nothing else (Decision 1)."""

from __future__ import annotations

import json

import pytest

from backend.closing import FALLBACK_LINE, problem
from backend.language import closing_language_line, question_language_line
from backend.prompts.language_prompts import TAGLISH_FALLBACK_LINE, TAGLISH_LINE
from backend.tests.conftest import make_pdf, upload, wait_for

TAGLISH = json.dumps({"panel_language": "taglish"})
# Roles whose prompts must stay English: scoring, the debrief and its suggestions, and setup.
ENGLISH_ROLES = ("extraction", "persona", "scoring", "suggestions", "narrative")


@pytest.fixture
def long_pdf(tmp_path):
    return make_pdf(tmp_path / "Library System.pdf", pages=16)


def _run(client, pdf, panel: str | None = None) -> list[dict]:
    session_id = upload(client, pdf, panel=panel).json()["session_id"]
    wait_for(client, session_id, lambda s: s.get("status") == "ready")
    start = client.post(f"/sessions/{session_id}/start")
    assert start.status_code == 200, start.text
    answers = []
    while not answers or not answers[-1]["done"]:
        response = client.post(f"/sessions/{session_id}/answers", json={"text": "Because thirty people answered."})
        assert response.status_code == 200, response.text
        answers.append(response.json())
    return answers


def _paths(gemini) -> set[str]:
    """Which question paths the session's prompts went through."""
    kinds = set()
    for prompt in gemini.question_prompts:
        if "the Devil's Advocate on" in prompt:
            kinds.add("devils_advocate")
        elif "The specific weakness identified in their answer" in prompt:
            kinds.add("follow_up")
        else:
            kinds.add("new_topic")
    return kinds


# --- the language line ---


def test_english_fills_both_slots_with_nothing_and_taglish_with_the_one_line():
    assert question_language_line("english") == "" == closing_language_line("english")
    assert question_language_line("taglish") == f"\n- Language: {TAGLISH_LINE}"
    assert closing_language_line("taglish") == f"\n{TAGLISH_LINE}\n"


def test_the_taglish_fixed_line_passes_the_closing_checks():
    assert problem(TAGLISH_FALLBACK_LINE) is None
    assert TAGLISH_FALLBACK_LINE != FALLBACK_LINE


# --- the flag ---


@pytest.mark.parametrize("on", [True, False])
def test_status_says_whether_taglish_mode_is_on(client_factory, on):
    with client_factory(taglish_mode=on) as client:
        assert client.get("/status").json()["taglish_mode"] is on


def test_with_the_flag_off_a_taglish_upload_is_refused(client_factory, pdf):
    with client_factory(taglish_mode=False) as client:
        refused = upload(client, pdf, panel=TAGLISH)
    assert refused.status_code == 422
    assert refused.json()["detail"] == "Taglish mode is off on this server, so the panel speaks English only."


# Every accepted upload here is waited on until ingest ends: a TestClient that shuts down
# mid-ingest hangs, on the pre-M16 parent too (a known harness bug, ROADMAP).
def test_with_the_flag_off_an_english_upload_still_works(client_factory, pdf):
    with client_factory(taglish_mode=False) as client:
        english = upload(client, pdf, panel=json.dumps({"panel_language": "english"}))
        assert english.status_code == 202, english.text
        wait_for(client, english.json()["session_id"], lambda s: s.get("status") == "ready")


def test_with_the_flag_on_a_taglish_panel_is_stored_and_omitting_the_field_is_english(client_factory, pdf):
    with client_factory(taglish_mode=True) as client:
        taglish = upload(client, pdf, panel=TAGLISH).json()["session_id"]
        default = upload(client, pdf).json()["session_id"]
        for session_id in (taglish, default):
            wait_for(client, session_id, lambda s: s.get("status") == "ready")
        store = client.app.state.store
        assert store.get(taglish).panel.panel_language == "taglish"
        assert store.get(default).panel.panel_language == "english"


def test_an_unknown_language_is_a_validation_error(client_factory, pdf):
    with client_factory(taglish_mode=True) as client:
        response = upload(client, pdf, panel=json.dumps({"panel_language": "tagalog"}))
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["panel_language"]


# --- the threading, end to end ---


def test_a_taglish_defense_puts_the_line_in_every_question_path_and_closing_and_nowhere_else(
    client_factory, gemini, long_pdf
):
    gemini.strong_answers = False  # follow-ups, hand-offs and the Devil's Advocate all happen
    with client_factory(taglish_mode=True) as client:
        answers = _run(client, long_pdf, panel=TAGLISH)
    assert _paths(gemini) == {"new_topic", "follow_up", "devils_advocate"}
    assert all(p.count(TAGLISH_LINE) == 1 for p in gemini.question_prompts)
    assert all(f"\n- Language: {TAGLISH_LINE}" in p for p in gemini.question_prompts)
    assert gemini.closing_prompts and all(p.count(TAGLISH_LINE) == 1 for p in gemini.closing_prompts)
    for role in ENGLISH_ROLES:
        assert gemini.prompts_by_role[role], role
        assert not any("Taglish" in p for p in gemini.prompts_by_role[role]), role
    assert answers[-1]["done"] is True


def test_an_english_defense_has_no_language_line_anywhere(client_factory, gemini, long_pdf):
    gemini.strong_answers = False
    with client_factory(taglish_mode=True) as client:
        _run(client, long_pdf)
    assert _paths(gemini) == {"new_topic", "follow_up", "devils_advocate"}
    every_prompt = [p for prompts in gemini.prompts_by_role.values() for p in prompts]
    assert every_prompt and not any("Taglish" in p or "- Language:" in p for p in every_prompt)


@pytest.mark.parametrize(
    ("panel", "line"), [(TAGLISH, TAGLISH_FALLBACK_LINE), (None, FALLBACK_LINE)], ids=["taglish", "english"]
)
def test_the_fixed_closing_line_is_in_the_panels_language(client_factory, gemini, long_pdf, panel, line):
    with client_factory(taglish_mode=True, closing_lines="fixed") as client:
        answers = _run(client, long_pdf, panel=panel)
    closings = [a["closing"]["line"] for a in answers if a["closing"] is not None]
    assert closings and set(closings) == {line}
    assert gemini.calls_by_role["closing"] == 0


def test_a_failed_taglish_closing_call_falls_back_to_the_taglish_line(client_factory, gemini, long_pdf):
    gemini.closing_line = "Ano ang sagot mo?"  # a question: invalid, so the fallback is said
    with client_factory(taglish_mode=True) as client:
        answers = _run(client, long_pdf, panel=TAGLISH)
    closings = [a["closing"]["line"] for a in answers if a["closing"] is not None]
    assert closings and set(closings) == {TAGLISH_FALLBACK_LINE}
