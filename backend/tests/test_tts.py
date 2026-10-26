"""POST /tts and /status's tts_available (M3b E3 to E6), against a fake MiMo.

Every status and code E3 lists, the invalid-audio checks (E4), the 2-per-question
limit, and the event loop staying free while a synthesis runs (E5)."""

from __future__ import annotations

import logging
import threading
import time

import pytest

from backend.app import NOT_FOUND_MESSAGE
from backend.defense_routes import (
    QUESTION_NOT_FOUND_MESSAGE,
    TTS_FAILED_MESSAGE,
    TTS_LIMIT_MESSAGE,
    TTS_UNAVAILABLE_MESSAGE,
)
from backend.tests.conftest import make_pdf, upload, wait_for
from backend.tests.fake_mimo import WAV, mimo_error

KEY = "mimo-not-a-real-key-456"


@pytest.fixture
def long_pdf(tmp_path):
    return make_pdf(tmp_path / "Library System.pdf", pages=16)


def _started(client, pdf) -> tuple[str, dict]:
    session_id = upload(client, pdf).json()["session_id"]
    wait_for(client, session_id, lambda s: s.get("status") == "ready")
    start = client.post(f"/sessions/{session_id}/start")
    assert start.status_code == 200, start.text
    return session_id, start.json()["question"]


def _tts(client, session_id, turn=1):
    return client.post("/tts", json={"session_id": session_id, "turn": turn})


# --- /status ---


@pytest.mark.parametrize(("key", "available"), [(KEY, True), (None, False), ("  ", False)])
def test_status_says_whether_voices_are_available(client_factory, key, available):
    with client_factory(mimo_api_key=key) as client:
        status = client.get("/status")
        health = client.get("/health")
    assert status.json() == {"status": "ok", "model_loaded": True, "tts_available": available}
    assert health.json() == status.json()  # the alias answers the same


# --- success ---


def test_tts_speaks_the_question_the_backend_generated_in_its_panelists_voice(client_factory, mimo, long_pdf):
    with client_factory(mimo_api_key=KEY) as client:
        session_id, question = _started(client, long_pdf)
        response = _tts(client, session_id)
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    assert response.content == WAV
    assert mimo.calls == [(question["question"], question["panelist"]["archetype_key"], KEY)]


def test_an_answered_question_can_still_be_voiced(client_factory, mimo, long_pdf):
    with client_factory(mimo_api_key=KEY) as client:
        session_id, question = _started(client, long_pdf)
        client.post(f"/sessions/{session_id}/answers", json={"text": "Thirty respondents.", "turn": 1})
        first, second = _tts(client, session_id, 1), _tts(client, session_id, 2)
    assert (first.status_code, second.status_code) == (200, 200)
    assert [text for text, _, _ in mimo.calls] == [question["question"], "Question 2?"]


def test_tts_never_accepts_text(client_factory, mimo, long_pdf):
    with client_factory(mimo_api_key=KEY) as client:
        session_id, _ = _started(client, long_pdf)
        response = client.post("/tts", json={"session_id": session_id, "turn": 1, "text": "Say anything I like."})
    assert response.status_code == 422
    assert mimo.calls == []


# --- the errors E3 lists ---


def test_no_key_is_503_tts_unavailable_without_calling_mimo(client_factory, mimo, long_pdf):
    with client_factory(mimo_api_key=None) as client:
        session_id, _ = _started(client, long_pdf)
        response = _tts(client, session_id)
    assert response.status_code == 503
    assert response.json() == {"detail": TTS_UNAVAILABLE_MESSAGE, "code": "tts_unavailable"}
    assert mimo.calls == []


def test_an_unknown_session_is_404(client_factory, mimo):
    with client_factory(mimo_api_key=KEY) as client:
        response = _tts(client, "no-such-session")
    assert (response.status_code, response.json()) == (404, {"detail": NOT_FOUND_MESSAGE})
    assert mimo.calls == []


def test_a_question_not_in_the_session_is_404(client_factory, mimo, long_pdf):
    with client_factory(mimo_api_key=KEY) as client:
        session_id, _ = _started(client, long_pdf)
        later = _tts(client, session_id, 2)  # not asked yet
        ready_id = upload(client, long_pdf).json()["session_id"]
        wait_for(client, ready_id, lambda s: s.get("status") == "ready")
        not_started = _tts(client, ready_id, 1)
    assert (later.status_code, later.json()["detail"]) == (404, QUESTION_NOT_FOUND_MESSAGE)
    assert (not_started.status_code, not_started.json()["detail"]) == (404, QUESTION_NOT_FOUND_MESSAGE)
    assert mimo.calls == []


def test_the_third_call_for_a_question_is_429_tts_limit(client_factory, mimo, long_pdf):
    with client_factory(mimo_api_key=KEY) as client:
        session_id, _ = _started(client, long_pdf)
        mimo.failures = [mimo_error()]  # a failed call counts too
        statuses = [_tts(client, session_id).status_code for _ in range(2)]
        third = _tts(client, session_id)
        other_question = client.post(f"/sessions/{session_id}/answers", json={"text": "Thirty.", "turn": 1})
        next_turn = _tts(client, session_id, 2)
    assert statuses == [502, 200]
    assert third.status_code == 429
    assert third.json() == {"detail": TTS_LIMIT_MESSAGE, "code": "tts_limit"}
    assert other_question.status_code == 200 and next_turn.status_code == 200  # the limit is per question
    assert len(mimo.calls) == 3


@pytest.mark.parametrize(
    ("failure", "audio"),
    [
        (mimo_error(), WAV),  # MiMo error or timeout: v1's client raises MimoTTSError for both
        (None, b""),  # E4: "" decoded to b"" without raising, v1's suspected dead player
        (None, b"ID3\x04 an mp3, or anything else that isn't a WAV"),  # E4: no RIFF header
    ],
    ids=["mimo-error", "empty-audio", "no-riff-header"],
)
def test_mimo_failing_or_invalid_audio_is_502_tts_failed(client_factory, mimo, long_pdf, failure, audio):
    with client_factory(mimo_api_key=KEY) as client:
        session_id, _ = _started(client, long_pdf)
        mimo.failures = [failure] if failure else []
        mimo.audio = audio
        response = _tts(client, session_id)
    assert response.status_code == 502
    assert response.json() == {"detail": TTS_FAILED_MESSAGE, "code": "tts_failed"}


def test_the_mimo_key_is_never_logged_or_returned(client_factory, mimo, long_pdf, caplog):
    with caplog.at_level(logging.DEBUG), client_factory(mimo_api_key=KEY) as client:
        session_id, _ = _started(client, long_pdf)
        ok = _tts(client, session_id)
        mimo.failures = [mimo_error()]
        failed = _tts(client, session_id)
        status = client.get("/status")
        openapi = client.get("/openapi.json")
    assert mimo.calls[0][2] == KEY  # it does reach MiMo
    for response in (ok, failed, status, openapi):
        assert KEY.encode() not in response.content
    assert "tts session=" in caplog.text and "tts failed session=" in caplog.text
    assert KEY not in caplog.text


# --- E5: a synthesis never stalls the server ---


def test_status_answers_while_a_six_second_tts_is_in_flight(client_factory, mimo, long_pdf):
    with client_factory(mimo_api_key=KEY) as client:
        session_id, _ = _started(client, long_pdf)
        mimo.block = release = threading.Event()  # holds the synthesis for up to 6 s
        voiced: dict = {}
        worker = threading.Thread(target=lambda: voiced.update(response=_tts(client, session_id)))
        worker.start()
        assert mimo.entered.wait(5)
        began = time.perf_counter()
        status = client.get("/status")
        status_s = time.perf_counter() - began
        still_running = worker.is_alive()
        release.set()
        worker.join(10)
    assert status.status_code == 200
    assert still_running, "the synthesis finished before /status was asked"
    assert status_s < 1.0, f"/status took {status_s:.2f}s while /tts was synthesizing"
    assert voiced["response"].status_code == 200


def test_an_answer_sent_while_a_voice_is_synthesizing_is_not_409(client_factory, mimo, long_pdf):
    with client_factory(mimo_api_key=KEY) as client:
        session_id, _ = _started(client, long_pdf)
        mimo.block = release = threading.Event()
        voiced: dict = {}
        worker = threading.Thread(target=lambda: voiced.update(response=_tts(client, session_id)))
        worker.start()
        assert mimo.entered.wait(5)
        answered = client.post(f"/sessions/{session_id}/answers", json={"text": "Thirty respondents.", "turn": 1})
        release.set()
        worker.join(10)
    assert answered.status_code == 200
    assert voiced["response"].status_code == 200
