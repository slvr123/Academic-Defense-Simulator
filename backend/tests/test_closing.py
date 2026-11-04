"""M15: the outgoing panelist's closing line on a hand-off. When it happens (Decision 1),
the call alongside the next question (Decision 2), the fallback that never blocks a
turn (Decision 4), the API fields (Decision 5) and the three flag values."""

from __future__ import annotations

import logging
import threading
import time

import pytest

from academic_defense_simulator.llm.provider import LLMServiceUnavailableError
from academic_defense_simulator.models.defense_profile import DefenseProfile, DefenseType
from academic_defense_simulator.models.panelist import Panelist
from backend.closing import FALLBACK_LINE, GRACE_S, closing_prompt, problem
from backend.tests.conftest import make_pdf, upload, wait_for

KEY = "test-mimo-key"


@pytest.fixture
def long_pdf(tmp_path):
    return make_pdf(tmp_path / "Library System.pdf", pages=16)


def _start(client, pdf) -> tuple[str, dict]:
    session_id = upload(client, pdf).json()["session_id"]
    wait_for(client, session_id, lambda s: s.get("status") == "ready")
    start = client.post(f"/sessions/{session_id}/start")
    assert start.status_code == 200, start.text
    return session_id, start.json()


def _answer(client, session_id, text="Because the evaluation used thirty respondents."):
    response = client.post(f"/sessions/{session_id}/answers", json={"text": text})
    assert response.status_code == 200, response.text
    return response.json()


def _run(client, session_id) -> list[dict]:
    answers = []
    while True:
        answers.append(_answer(client, session_id))
        if answers[-1]["done"]:
            return answers


# --- Decision 1: when a closing line happens ---


def test_every_hand_off_gets_the_outgoing_panelists_closing_and_nothing_else_does(client_factory, gemini, long_pdf):
    gemini.strong_answers = False  # every panelist follows up twice, so follow-ups and hand-offs alternate
    with client_factory() as client:
        session_id, start = _start(client, long_pdf)
        answers = _run(client, session_id)
    assert "closing" not in start  # the first question has no one before it
    asked = [start["question"]] + [a["next_question"] for a in answers[:-1]]
    for answered, response in zip(asked, answers, strict=True):
        following = response["next_question"]
        hand_off = following is not None and following["panelist"]["archetype_key"] != answered["panelist"]["archetype_key"]
        if hand_off:
            assert response["closing"] == {"panelist": answered["panelist"], "line": gemini.closing_line}
        else:
            assert response["closing"] is None  # a follow-up, or the end of the defense
    hand_offs = [a for a in answers if a["closing"] is not None]
    assert len(answers) == 12 and len(hand_offs) == 3 == gemini.calls_by_role["closing"]
    assert answers[-1]["done"] is True and answers[-1]["closing"] is None


def test_the_hand_off_to_the_terror_panelist_is_closed_too_and_titles_are_the_display_ones(client_factory, gemini, long_pdf):
    with client_factory() as client:
        session_id, _start_body = _start(client, long_pdf)
        answers = _run(client, session_id)
    to_terror = [a for a in answers if a["next_question"] and a["next_question"]["panelist"]["archetype_key"] == "devils_advocate"]
    [closing] = [a["closing"] for a in to_terror]
    assert closing["panelist"]["archetype_key"] != "devils_advocate"
    assert all(a["closing"] is None or a["closing"]["panelist"]["title"] != "Devil's Advocate" for a in answers)


def test_the_prompt_carries_the_outgoing_voice_and_the_exchange():
    profile = DefenseProfile(defense_type=DefenseType.THESIS, domain="IT", topic="AniCheck", selected_archetypes=["methodology_expert"], document_id="d")
    panelist = Panelist(archetype_key="methodology_expert", panelist_name="Lim", persona_framing="A patient statistician.", icon="engineer")
    prompt = closing_prompt(panelist, profile, "How did you sample?", "We used thirty farmers.")
    for part in ("Dr. Lim", "Methodology Expert", "thesis defense", "A patient statistician.", "AniCheck", "How did you sample?", "We used thirty farmers."):
        assert part in prompt
    assert "Do not evaluate the answer" in prompt and "Do not ask anything" in prompt
    assert "already said" not in prompt  # no earlier lines, no block


def test_each_closing_prompt_sees_the_sessions_earlier_lines_verbatim_and_in_order(client_factory, gemini, long_pdf):
    """M15 amendment: so a panel doesn't open every closing line the same way."""
    gemini.strong_answers = False
    with client_factory() as client:
        session_id, _start_body = _start(client, long_pdf)
        lines = iter(["First, about the scope.", "Second, on sampling.", "Third, on results."])
        said = []
        gemini.closing_line = next(lines)
        while True:
            response = _answer(client, session_id)
            if response["closing"] is not None:
                said.append(response["closing"]["line"])
                gemini.closing_line = next(lines, "unused")
            if response["done"]:
                break
    first, second, third = gemini.closing_prompts
    assert said == ["First, about the scope.", "Second, on sampling.", "Third, on results."]
    assert "already said" not in first
    assert '- "First, about the scope."' in second and "Second, on sampling." not in second
    assert '- "First, about the scope."\n- "Second, on sampling."' in third
    assert "Do not begin your closing line with the same opening words as any of them." in third


# --- Decision 2: concurrency ---


def test_the_closing_call_runs_while_the_next_question_is_being_written(client_factory, gemini, long_pdf):
    with client_factory() as client:
        session_id, _start_body = _start(client, long_pdf)
        gemini.block["question"] = threading.Event()  # the next question is held mid-generation
        result: dict = {}
        sender = threading.Thread(target=lambda: result.update(_answer(client, session_id)))
        sender.start()
        began = gemini.entered["closing"].wait(3)  # the closing call began...
        writing = sender.is_alive()  # ...while the question was still being written
        gemini.block["question"].set()  # released before asserting, so a failure can't hang the test
        sender.join(15)
        assert began and writing
    assert result["closing"]["line"] == gemini.closing_line


def test_a_slow_closing_call_holds_the_turn_up_at_most_the_grace_period(client_factory, gemini, long_pdf, caplog):
    with client_factory() as client:
        session_id, _start_body = _start(client, long_pdf)
        gemini.block["closing"] = threading.Event()  # never answers in time
        started = time.perf_counter()
        with caplog.at_level(logging.INFO, logger="backend.closing"):
            body = _answer(client, session_id)
        took = time.perf_counter() - started
        gemini.block["closing"].set()
    assert body["next_question"] is not None
    assert body["closing"]["line"] == FALLBACK_LINE
    assert GRACE_S <= took < GRACE_S + 3
    [line] = [r.getMessage() for r in caplog.records if r.getMessage().startswith("closing ")]
    assert "source=fixed reason=timeout" in line


# --- Decision 4: failure never blocks a turn ---


def test_a_failed_closing_call_still_delivers_the_next_question_with_the_fallback(client_factory, gemini, long_pdf, caplog):
    answer_text = "Because the evaluation used thirty respondents."
    with client_factory() as client:
        session_id, _start_body = _start(client, long_pdf)
        gemini.fail_next("closing", LLMServiceUnavailableError("503 UNAVAILABLE, retried once", model="m"))
        with caplog.at_level(logging.INFO, logger="backend.closing"):
            body = _answer(client, session_id, answer_text)
    assert body["next_question"]["turn"] == 2
    assert body["closing"]["line"] == FALLBACK_LINE
    assert body["closing"]["panelist"]["archetype_key"] != body["next_question"]["panelist"]["archetype_key"]
    [line] = [r.getMessage() for r in caplog.records if r.getMessage().startswith("closing ")]
    assert "source=fixed reason=error:LLMServiceUnavailableError" in line
    assert answer_text not in caplog.text and gemini.closing_line not in caplog.text  # no answer or line text logged


@pytest.mark.parametrize(
    ("said", "reason"),
    [
        (" ".join(["word"] * 31) + ".", "too_long"),
        ("Alright. I'll stop. Thank you. Over to you.", "too_many_sentences"),
        ("Shall we move on to your results?", "asks_a_question"),
        ("   ", "empty"),
    ],
)
def test_an_invalid_closing_line_is_replaced_by_the_fallback(client_factory, gemini, long_pdf, caplog, said, reason):
    gemini.closing_line = said
    with client_factory() as client:
        session_id, _start_body = _start(client, long_pdf)
        with caplog.at_level(logging.INFO, logger="backend.closing"):
            body = _answer(client, session_id)
    assert body["closing"]["line"] == FALLBACK_LINE
    assert f"source=fixed reason={reason}" in caplog.text


@pytest.mark.parametrize(
    ("line", "verdict"),
    [
        ("Alright, that's what I wanted to understand about your sampling. I'll leave it there.", None),
        ("Thank you", None),
        ("One. Two. Three.", "too_many_sentences"),
        (" ".join(["w"] * 30), None),
        (" ".join(["w"] * 31), "too_long"),
        ("Fair?", "asks_a_question"),
    ],
)
def test_the_rule_is_one_or_two_sentences_of_at_most_30_words(line, verdict):
    assert problem(line) == verdict


# --- the flag ---


def test_fixed_says_the_fallback_on_every_hand_off_with_no_call(client_factory, gemini, long_pdf):
    with client_factory(closing_lines="fixed") as client:
        session_id, _start_body = _start(client, long_pdf)
        answers = _run(client, session_id)
    lines = [a["closing"]["line"] for a in answers if a["closing"] is not None]
    assert lines == [FALLBACK_LINE] * 3
    assert gemini.calls_by_role.get("closing", 0) == 0


def test_off_says_nothing_and_calls_nothing(client_factory, gemini, long_pdf):
    with client_factory(closing_lines="off") as client:
        session_id, _start_body = _start(client, long_pdf)
        answers = _run(client, session_id)
    assert all(a["closing"] is None for a in answers)
    assert gemini.calls_by_role.get("closing", 0) == 0


def test_llm_is_the_default(client_factory):
    with client_factory() as client:
        assert client.app.state.settings.closing_lines == "llm"


# --- Decision 5: the API carries the lines wherever it carries questions ---


def test_current_and_the_transcript_carry_the_closing_lines(client_factory, gemini, long_pdf):
    with client_factory() as client:
        session_id, _start_body = _start(client, long_pdf)
        first = _answer(client, session_id)
        current = client.get(f"/sessions/{session_id}/current").json()
        answers = [first] + _run(client, session_id)
        report = client.get(f"/sessions/{session_id}/report").json()
    assert current["closing"] == first["closing"] is not None
    assert [t["closing"] for t in report["transcript"]] == [a["closing"] for a in answers]


def test_a_replayed_answer_replays_its_closing_line(client_factory, gemini, long_pdf):
    with client_factory() as client:
        session_id, _start_body = _start(client, long_pdf)
        first = client.post(f"/sessions/{session_id}/answers", json={"text": "An answer.", "turn": 1}).json()
        again = client.post(f"/sessions/{session_id}/answers", json={"text": "An answer.", "turn": 1}).json()
    assert again == first and first["closing"] is not None
    assert gemini.calls_by_role["closing"] == 1


def test_tts_speaks_a_closing_line_in_the_outgoing_panelists_voice(client_factory, gemini, mimo, long_pdf):
    with client_factory(mimo_api_key=KEY) as client:
        session_id, start = _start(client, long_pdf)
        first = _answer(client, session_id)
        spoken = client.post("/tts", json={"session_id": session_id, "turn": 1, "closing": True})
        question = client.post("/tts", json={"session_id": session_id, "turn": 1})
        missing = client.post("/tts", json={"session_id": session_id, "turn": 2, "closing": True})
    assert spoken.status_code == 200 and question.status_code == 200
    assert mimo.calls[0] == (first["closing"]["line"], start["question"]["panelist"]["archetype_key"], KEY)
    assert mimo.calls[1][0] == start["question"]["question"]  # the question's own allowance is separate
    assert missing.status_code == 404  # turn 2 hasn't been answered, so nothing closed it
