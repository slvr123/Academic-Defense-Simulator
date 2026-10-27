"""M6 H9: POST /tts with `part` voices one sentence at a time, behind
ADS_TTS_SPLIT_SENTENCES, with 2 synth calls per part and at most MAX_PARTS parts.
M6 H8: the /tts log line carries timings, sizes and positions, never the text."""

from __future__ import annotations

import logging

import pytest

from backend.defense import AskedQuestion
from backend.defense_routes import QUESTION_NOT_FOUND_MESSAGE, TTS_LIMIT_MESSAGE
from backend.tests.conftest import make_pdf, upload, wait_for
from backend.tests.fake_mimo import WAV
from backend.tts_split import MAX_PARTS, split_sentences

KEY = "mimo-not-a-real-key-789"
QUESTION = (
    "Your evaluation used thirty respondents from one school. "
    "Why did you treat that sample as enough for an ISO 25010 claim? "
    "Explain what a second site would have changed in the usability score."
)


@pytest.fixture
def long_pdf(tmp_path):
    return make_pdf(tmp_path / "Library System.pdf", pages=16)


def _started(client, pdf) -> str:
    session_id = upload(client, pdf).json()["session_id"]
    wait_for(client, session_id, lambda s: s.get("status") == "ready")
    assert client.post(f"/sessions/{session_id}/start").status_code == 200
    # A known three-sentence question in place of the fake panel's own.
    defense = client.app.state.store.get(session_id).defense
    defense.asked[1] = AskedQuestion(text=QUESTION, archetype_key=defense.asked[1].archetype_key)
    return session_id


def _tts(client, session_id, part=None, turn=1):
    body = {"session_id": session_id, "turn": turn}
    return client.post("/tts", json=body if part is None else {**body, "part": part})


def test_with_the_flag_on_each_part_is_one_sentence(client_factory, mimo, long_pdf):
    sentences = split_sentences(QUESTION)
    assert len(sentences) == 3
    with client_factory(mimo_api_key=KEY, tts_split_sentences=True) as client:
        session_id = _started(client, long_pdf)
        responses = [_tts(client, session_id, part) for part in range(3)]
        past_the_end = _tts(client, session_id, 3)
    assert [r.status_code for r in responses] == [200, 200, 200]
    assert [r.headers["x-tts-parts"] for r in responses] == ["3", "3", "3"]
    assert all(r.content == WAV and r.headers["content-type"] == "audio/wav" for r in responses)
    assert [text for text, _, _ in mimo.calls] == sentences
    assert (past_the_end.status_code, past_the_end.json()["detail"]) == (404, QUESTION_NOT_FOUND_MESSAGE)
    assert len(mimo.calls) == 3  # the refused part cost nothing


def test_with_the_flag_off_part_0_is_the_whole_question(client_factory, mimo, long_pdf):
    with client_factory(mimo_api_key=KEY) as client:  # tts_split_sentences defaults to False
        session_id = _started(client, long_pdf)
        first = _tts(client, session_id, 0)
        second = _tts(client, session_id, 1)
    assert (first.status_code, first.headers["x-tts-parts"]) == (200, "1")
    assert mimo.calls[0][0] == QUESTION
    assert second.status_code == 404 and len(mimo.calls) == 1


def test_without_part_nothing_changes(client_factory, mimo, long_pdf):
    with client_factory(mimo_api_key=KEY, tts_split_sentences=True) as client:
        session_id = _started(client, long_pdf)
        whole = _tts(client, session_id)
    assert whole.status_code == 200 and "x-tts-parts" not in whole.headers
    assert mimo.calls[0][0] == QUESTION


def test_each_part_gets_its_own_two_calls(client_factory, mimo, long_pdf):
    with client_factory(mimo_api_key=KEY, tts_split_sentences=True) as client:
        session_id = _started(client, long_pdf)
        part_0 = [_tts(client, session_id, 0).status_code for _ in range(3)]
        part_1 = [_tts(client, session_id, 1).status_code for _ in range(2)]
        whole = [_tts(client, session_id).status_code for _ in range(3)]
        third = _tts(client, session_id, 0)
    assert part_0 == [200, 200, 429] and part_1 == [200, 200] and whole == [200, 200, 429]
    assert third.json() == {"detail": TTS_LIMIT_MESSAGE, "code": "tts_limit"}


@pytest.mark.parametrize("part", [-1, MAX_PARTS])
def test_a_part_outside_0_to_max_parts_is_422(client_factory, mimo, long_pdf, part):
    with client_factory(mimo_api_key=KEY, tts_split_sentences=True) as client:
        session_id = _started(client, long_pdf)
        assert _tts(client, session_id, part).status_code == 422
    assert mimo.calls == []


def test_the_part_header_is_readable_cross_origin(client_factory, mimo, long_pdf):
    origin = "http://localhost:5173"
    with client_factory(mimo_api_key=KEY, tts_split_sentences=True, cors_origins=origin) as client:
        session_id = _started(client, long_pdf)
        response = client.post("/tts", json={"session_id": session_id, "turn": 1, "part": 0}, headers={"Origin": origin})
    exposed = {h.strip().lower() for h in response.headers["access-control-expose-headers"].split(",")}
    assert "x-tts-parts" in exposed


def test_the_log_line_has_timings_and_never_the_text(client_factory, mimo, long_pdf, caplog):
    caplog.set_level(logging.INFO)
    with client_factory(mimo_api_key=KEY, tts_split_sentences=True) as client:
        session_id = _started(client, long_pdf)
        _tts(client, session_id, 1)
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("tts session=")]
    assert len(lines) == 1
    line = lines[0]
    assert "part=1/3" in line and f"chars={len(split_sentences(QUESTION)[1])}" in line and " ms=" in line
    for sentence in split_sentences(QUESTION):
        assert sentence not in line
    assert "respondents" not in "\n".join(r.getMessage() for r in caplog.records)
