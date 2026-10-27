"""M6 H4 and H5: a lost /start response replays, and GET /current lets a client
whose answer was stale move to the open question."""

from __future__ import annotations

from datetime import date

import pytest

from backend.defense_routes import ALREADY_STARTED_MESSAGE, NOT_STARTED_MESSAGE, STALE_QUESTION_MESSAGE
from backend.tests.conftest import make_pdf, upload, wait_for

TODAY = date(2026, 10, 20)  # the FakeClock's Pacific day


@pytest.fixture
def long_pdf(tmp_path):
    return make_pdf(tmp_path / "Library System.pdf", pages=16)


def _ready(client, pdf) -> str:
    session_id = upload(client, pdf).json()["session_id"]
    wait_for(client, session_id, lambda s: s.get("status") == "ready")
    return session_id


def _answer(client, session_id, turn=None):
    body = {"text": "Because the evaluation used thirty respondents."}
    return client.post(f"/sessions/{session_id}/answers", json=body if turn is None else {**body, "turn": turn})


def _current(client, session_id):
    return client.get(f"/sessions/{session_id}/current")


# --- H4: /start replay ------------------------------------------------------------


def test_a_resent_start_replays_with_no_gemini_call_and_no_second_slot(client_factory, gemini, quota_store, long_pdf):
    with client_factory() as client:
        session_id = _ready(client, long_pdf)
        first = client.post(f"/sessions/{session_id}/start")
        calls = dict(gemini.calls_by_role)
        resent = [client.post(f"/sessions/{session_id}/start") for _ in range(2)]
    assert first.status_code == 200
    assert all((r.status_code, r.json()) == (200, first.json()) for r in resent)
    assert dict(gemini.calls_by_role) == calls
    assert quota_store.used(TODAY) == 1


def test_once_an_answer_is_in_a_resent_start_is_already_started(client_factory, gemini, long_pdf):
    with client_factory() as client:
        session_id = _ready(client, long_pdf)
        assert client.post(f"/sessions/{session_id}/start").status_code == 200
        assert _answer(client, session_id, 1).status_code == 200
        calls = dict(gemini.calls_by_role)
        late = client.post(f"/sessions/{session_id}/start")
    assert (late.status_code, late.json()) == (409, {"detail": ALREADY_STARTED_MESSAGE, "code": "already_started"})
    assert dict(gemini.calls_by_role) == calls


# --- H5: GET /current -----------------------------------------------------------------


def test_current_follows_the_defense_from_start_to_done(client_factory, long_pdf):
    with client_factory() as client:
        session_id = _ready(client, long_pdf)
        before = _current(client, session_id)
        start = client.post(f"/sessions/{session_id}/start").json()
        at_one = _current(client, session_id).json()
        next_question = _answer(client, session_id, 1).json()["next_question"]
        at_two = _current(client, session_id).json()
        while True:
            body = _answer(client, session_id).json()
            if body["done"]:
                break
        done = _current(client, session_id).json()
    assert (before.status_code, before.json()["detail"]) == (409, NOT_STARTED_MESSAGE)
    assert at_one == {"done": False, "question": start["question"]}
    assert at_two == {"done": False, "question": next_question} and next_question["turn"] == 2
    assert done == {"done": True, "question": None}


def test_after_a_stale_answer_current_names_the_open_question(client_factory, long_pdf):
    with client_factory() as client:
        session_id = _ready(client, long_pdf)
        client.post(f"/sessions/{session_id}/start")
        _answer(client, session_id, 1)
        _answer(client, session_id, 2)
        # A second tab still showing turn 1: neither the turn just answered (2) nor the open one (3).
        stale = _answer(client, session_id, 1)
        current = _current(client, session_id).json()
    assert (stale.status_code, stale.json()) == (409, {"detail": STALE_QUESTION_MESSAGE, "code": "stale_question"})
    assert current["done"] is False and current["question"]["turn"] == 3


def test_current_for_an_unknown_session_is_404(client_factory):
    with client_factory() as client:
        assert _current(client, "no-such-session").status_code == 404
