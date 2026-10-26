"""Idempotent answers (M3b E13): a resend of the turn just answered replays its
stored response with no new Gemini call; any other turn but the open one is 409
`stale_question`; leaving `turn` out keeps M2's behaviour."""

from __future__ import annotations

import threading

import pytest

from backend.defense_routes import STALE_QUESTION_MESSAGE, TURN_IN_PROGRESS_MESSAGE
from backend.tests.conftest import make_pdf, upload, wait_for


@pytest.fixture
def long_pdf(tmp_path):
    return make_pdf(tmp_path / "Library System.pdf", pages=16)


def _started(client, pdf) -> str:
    session_id = upload(client, pdf).json()["session_id"]
    wait_for(client, session_id, lambda s: s.get("status") == "ready")
    assert client.post(f"/sessions/{session_id}/start").status_code == 200
    return session_id


def _answer(client, session_id, turn, text="Because the evaluation used thirty respondents."):
    body = {"text": text} if turn is None else {"text": text, "turn": turn}
    return client.post(f"/sessions/{session_id}/answers", json=body)


def test_resending_the_turn_just_answered_replays_its_response(client_factory, gemini, long_pdf):
    with client_factory() as client:
        session_id = _started(client, long_pdf)
        first = _answer(client, session_id, 1)
        calls_after_first = dict(gemini.calls_by_role)
        resent = _answer(client, session_id, 1, text="A different text: the server must not re-score it.")
        state = client.get(f"/sessions/{session_id}").json()
    assert first.status_code == 200 and first.json()["next_question"]["turn"] == 2
    assert (resent.status_code, resent.json()) == (200, first.json())
    assert dict(gemini.calls_by_role) == calls_after_first  # no scoring, no question, no Gemini call
    assert state["defense"] == "in_progress"


def test_a_lost_response_to_the_final_answer_replays_instead_of_409(client_factory, gemini, long_pdf):
    with client_factory() as client:
        session_id = _started(client, long_pdf)
        turn, last = 1, None
        while True:
            last = _answer(client, session_id, turn)
            assert last.status_code == 200, last.text
            if last.json()["done"]:
                break
            turn = last.json()["next_question"]["turn"]
        calls = dict(gemini.calls_by_role)
        resent = _answer(client, session_id, turn)
        without_turn = _answer(client, session_id, None)
    assert (resent.status_code, resent.json()) == (200, last.json())
    assert dict(gemini.calls_by_role) == calls
    assert without_turn.status_code == 409  # an old caller still gets "the defense is finished"


@pytest.mark.parametrize("sent", [3, 99], ids=["a-future-turn", "a-turn-never-asked"])
def test_a_turn_that_is_neither_open_nor_just_answered_is_stale(client_factory, gemini, long_pdf, sent):
    with client_factory() as client:
        session_id = _started(client, long_pdf)
        _answer(client, session_id, 1)  # turn 2 is open, turn 1 just answered
        calls = dict(gemini.calls_by_role)
        response = _answer(client, session_id, sent)
    assert response.status_code == 409
    assert response.json() == {"detail": STALE_QUESTION_MESSAGE, "code": "stale_question"}
    assert dict(gemini.calls_by_role) == calls


def test_only_the_last_answered_turn_replays(client_factory, long_pdf):
    with client_factory() as client:
        session_id = _started(client, long_pdf)
        _answer(client, session_id, 1)
        _answer(client, session_id, 2)
        older = _answer(client, session_id, 1)
    assert (older.status_code, older.json()["code"]) == (409, "stale_question")


def test_answers_without_a_turn_still_work(client_factory, long_pdf):
    with client_factory() as client:
        session_id = _started(client, long_pdf)
        first = _answer(client, session_id, None)
        second = _answer(client, session_id, None)
    assert first.status_code == 200 and second.status_code == 200
    assert second.json()["evaluation"] is not None


def test_a_resend_while_the_original_still_runs_is_a_plain_409(client_factory, gemini, long_pdf):
    """The client tells this 409 from stale_question by its missing code, and waits."""
    with client_factory() as client:
        session_id = _started(client, long_pdf)
        gemini.block["scoring"] = release = threading.Event()
        first: dict = {}
        worker = threading.Thread(target=lambda: first.update(response=_answer(client, session_id, 1)))
        worker.start()
        assert gemini.entered["scoring"].wait(5)
        resent_early = _answer(client, session_id, 1)
        release.set()
        worker.join(10)
        resent_after = _answer(client, session_id, 1)
    assert resent_early.status_code == 409
    assert resent_early.json() == {"detail": TURN_IN_PROGRESS_MESSAGE}
    assert (resent_after.status_code, resent_after.json()) == (200, first["response"].json())
