"""Full defenses through the API against the fake Gemini: start, answers, report,
the 409s, the quota reservation, and Gemini failures mapped to "busy"."""

from __future__ import annotations

import threading
from datetime import date

import pytest

from academic_defense_simulator.call_budget import CallRole, calls_per_model, worst_case_calls
from academic_defense_simulator.llm.provider import (
    LLMProviderError,
    LLMQuotaExhaustedError,
    LLMServiceUnavailableError,
)
from backend.defense_routes import (
    ALREADY_STARTED_MESSAGE,
    DEFENSE_DONE_MESSAGE,
    FAILED_MESSAGE,
    NOT_READY_MESSAGE,
    NOT_STARTED_MESSAGE,
    PANEL_ERROR_MESSAGE,
    QUOTA_DOWN_MESSAGE,
    REPORT_NOT_READY_MESSAGE,
    TURN_IN_PROGRESS_MESSAGE,
)
from backend.llm_errors import PANEL_BUSY_MESSAGE
from backend.quota import QuotaUnavailable
from backend.tests.conftest import make_pdf, upload, wait_for

TODAY = date(2026, 10, 20)  # the FakeClock's Pacific day


@pytest.fixture
def long_pdf(tmp_path):
    return make_pdf(tmp_path / "Library System.pdf", pages=16)  # 8 chunks


def _ready_session(client, pdf, panel: str | None = None) -> str:
    session_id = upload(client, pdf, panel=panel).json()["session_id"]
    wait_for(client, session_id, lambda s: s.get("status") == "ready")
    return session_id


def _start(client, session_id):
    return client.post(f"/sessions/{session_id}/start")


def _answer(client, session_id, text="Because the evaluation used thirty respondents."):
    return client.post(f"/sessions/{session_id}/answers", json={"text": text})


def _run_to_end(client, session_id) -> list[dict]:
    responses = []
    while True:
        body = _answer(client, session_id).json()
        responses.append(body)
        if body["done"]:
            return responses


# --- a full defense ---


def test_a_full_defense_runs_start_to_report(client_factory, gemini, quota_store, long_pdf):
    with client_factory() as client:
        session_id = _ready_session(client, long_pdf)
        assert client.get(f"/sessions/{session_id}").json()["defense"] == "not_started"

        start = _start(client, session_id)
        assert start.status_code == 200, start.text
        body = start.json()
        assert (body["domain"], body["topic"]) == ("Library science", "A library management system")  # extracted
        assert [p["archetype_key"] for p in body["panel"]][-1] == "devils_advocate" and len(body["panel"]) == 4
        question = body["question"]
        assert question["turn"] == 1 and question["question"] == "Question 1?"
        [source] = question["sources"]
        assert source["origin"] == "new_topic" and 1 <= source["page_start"] <= source["page_end"] <= 16
        assert "ISO/IEC 25010" in source["text"] and -1 <= source["score"] <= 1
        assert client.get(f"/sessions/{session_id}").json()["defense"] == "in_progress"
        assert quota_store.used(TODAY) == 1

        answers = _run_to_end(client, session_id)
        # All strong: each of 3 domain panelists opens one topic, then Devil's Advocate once.
        assert len(answers) == 4
        assert [a["next_question"]["turn"] for a in answers[:-1]] == [2, 3, 4]
        assert answers[-2]["next_question"]["sources"][0]["origin"] == "devils_advocate"
        assert answers[-1]["next_question"] is None
        assert answers[0]["evaluation"] == {
            "clarity": 5, "depth": 5, "grounding": 5, "total": 15, "difficulty_delta": 1,
            "primary_gap": "no evidence offered", "answer_summary": "The candidate claimed the instrument is valid.",
        }

        report = client.get(f"/sessions/{session_id}/report")
        assert report.status_code == 200
        report = report.json()
        assert client.get(f"/sessions/{session_id}").json()["defense"] == "done"

    assert report["report"]["narrative"] == "The candidate defended the instrument choice well."
    assert report["report"]["difficulty_trajectory"] == [2, 3, 4, 5]  # +1 per strong answer, from 2
    assert [t["turn"] for t in report["transcript"]] == [1, 2, 3, 4]
    assert report["transcript"][0]["sources"] == [source]
    assert report["transcript"][0]["answer"] == "Because the evaluation used thirty respondents."
    assert quota_store.used(TODAY) == 1  # one reservation for the whole defense
    # extraction, persona, 4 questions, 4 scores, narrative, suggestions
    assert dict(gemini.calls_by_role) == {
        "extraction": 1, "persona": 1, "question": 4, "scoring": 4, "narrative": 1, "suggestions": 1,
    }


def test_weak_answers_draw_follow_ups_up_to_the_turn_cap(client_factory, gemini, long_pdf):
    gemini.strong_answers = False
    with client_factory() as client:
        session_id = _ready_session(client, long_pdf)
        first = _start(client, session_id).json()["question"]
        answers = _run_to_end(client, session_id)
        origins = [first["sources"][0]["origin"]] + [
            a["next_question"]["sources"][0]["origin"] for a in answers if a["next_question"]
        ]
    # Each panelist opens a topic and is pressed twice: 4 x 3 = t_max 12.
    assert origins == ["new_topic", "follow_up", "follow_up"] * 3 + ["devils_advocate"] * 3
    worst = calls_per_model(
        worst_case_calls(4, extraction=True, relevance_check=False), {r: "defense" for r in CallRole}
    )["defense"]
    assert sum(gemini.calls_by_role.values()) == 1 + 1 + 12 + 12 + 1 + 1 <= worst


def test_sent_domain_and_topic_skip_extraction(client_factory, gemini, long_pdf):
    panel = '{"domain": "Information systems", "topic": "DAZSMA library", "archetype_keys": ["methodology_expert"]}'
    with client_factory() as client:
        session_id = _ready_session(client, long_pdf, panel=panel)
        body = _start(client, session_id).json()
    assert (body["domain"], body["topic"]) == ("Information systems", "DAZSMA library")
    assert [p["archetype_key"] for p in body["panel"]] == ["methodology_expert", "devils_advocate"]
    assert "extraction" not in gemini.calls_by_role


def test_failed_extraction_falls_back_to_the_filename(client_factory, gemini, long_pdf):
    gemini.fail_next("extraction", LLMProviderError("timeout"), LLMProviderError("timeout"))  # v1 tries twice
    with client_factory() as client:
        body = _start(client, _ready_session(client, long_pdf)).json()
    assert (body["domain"], body["topic"]) == ("unspecified", "Library System")


# --- 409s and other refusals ---


def test_start_before_the_document_is_ready_is_409(client_factory, embedder, long_pdf):
    embedder.hold()
    with client_factory() as client:
        session_id = upload(client, long_pdf).json()["session_id"]
        response = _start(client, session_id)
        assert (response.status_code, response.json()["detail"]) == (409, NOT_READY_MESSAGE)


def test_start_on_a_failed_document_is_409(client_factory, tmp_path):
    with client_factory() as client:
        session_id = upload(client, make_pdf(tmp_path / "scan.pdf", pages=2, text=False)).json()["session_id"]
        wait_for(client, session_id, lambda s: s.get("status") == "failed")
        response = _start(client, session_id)
    assert (response.status_code, response.json()["detail"]) == (409, FAILED_MESSAGE)


def test_unknown_session_is_404(client_factory):
    with client_factory() as client:
        for response in (_start(client, "nope"), _answer(client, "nope"), client.get("/sessions/nope/report")):
            assert response.status_code == 404


def test_the_409s_around_a_defense(client_factory, quota_store, long_pdf):
    with client_factory() as client:
        session_id = _ready_session(client, long_pdf)
        assert _answer(client, session_id).json()["detail"] == NOT_STARTED_MESSAGE
        assert client.get(f"/sessions/{session_id}/report").json()["detail"] == NOT_STARTED_MESSAGE

        assert _start(client, session_id).status_code == 200
        again = _start(client, session_id)
        assert (again.status_code, again.json()["detail"]) == (409, ALREADY_STARTED_MESSAGE)
        assert quota_store.used(TODAY) == 1  # the second start reserved nothing
        early = client.get(f"/sessions/{session_id}/report")
        assert (early.status_code, early.json()["detail"]) == (409, REPORT_NOT_READY_MESSAGE)

        _run_to_end(client, session_id)
        after = _answer(client, session_id)
        assert (after.status_code, after.json()["detail"]) == (409, DEFENSE_DONE_MESSAGE)
        assert _start(client, session_id).status_code == 409


def test_blank_or_oversized_answers_are_422(client_factory, long_pdf):
    with client_factory() as client:
        session_id = _ready_session(client, long_pdf)
        _start(client, session_id)
        for text in ("", "   \n", "x" * 8001):
            assert _answer(client, session_id, text).status_code == 422


def test_a_document_too_short_for_the_panel_is_422_before_reserving(client_factory, quota_store, pdf):
    with client_factory() as client:  # conftest's pdf has 2 chunks; the default panel needs 3
        response = _start(client, _ready_session(client, pdf))
    assert response.status_code == 422 and "too few for 3 panelists" in response.json()["detail"]
    assert quota_store.used(TODAY) == 0


def test_a_second_request_while_a_turn_runs_is_409(client_factory, gemini, long_pdf):
    with client_factory() as client:
        session_id = _ready_session(client, long_pdf)
        _start(client, session_id)
        gemini.block["scoring"] = release = threading.Event()
        first: dict = {}
        worker = threading.Thread(target=lambda: first.update(response=_answer(client, session_id)))
        worker.start()
        assert gemini.entered["scoring"].wait(5)
        second = _answer(client, session_id)
        release.set()
        worker.join(10)
    assert (second.status_code, second.json()["detail"]) == (409, TURN_IN_PROGRESS_MESSAGE)
    assert first["response"].status_code == 200


# --- quota ---


def test_start_is_429_once_the_day_is_used_up(client_factory, gemini, quota_store, long_pdf):
    with client_factory() as client:
        # The day fills between the upload (whose check would refuse otherwise) and the start.
        session_id = _ready_session(client, long_pdf)
        for _ in range(11):
            quota_store.reserve(TODAY, 11)
        response = _start(client, session_id)
    assert response.status_code == 429 and "all used up" in response.json()["detail"]
    assert response.json()["code"] == "daily_cap"
    assert "retry-after" in response.headers
    assert sum(gemini.calls_by_role.values()) == 0  # refused before any Gemini call


def test_start_fails_closed_when_the_quota_store_is_down(client_factory, gemini, quota_store, long_pdf):
    with client_factory() as client:
        session_id = _ready_session(client, long_pdf)

        def down(day, cap):
            raise QuotaUnavailable("connection refused")

        quota_store.reserve = down
        response = _start(client, session_id)
        state = client.get(f"/sessions/{session_id}").json()
    assert (response.status_code, response.json()["detail"]) == (503, QUOTA_DOWN_MESSAGE)
    assert state["defense"] == "not_started" and sum(gemini.calls_by_role.values()) == 0


# --- Gemini failures ---


@pytest.mark.parametrize(
    "error",
    [LLMQuotaExhaustedError("429", model="gemini-3.1-flash-lite"), LLMServiceUnavailableError("503", model="m")],
    ids=["429", "503"],
)
@pytest.mark.parametrize("failing_call", ["scoring", "question"])
def test_a_busy_gemini_records_nothing_and_the_same_answer_can_be_resent(
    client_factory, gemini, quota_store, long_pdf, error, failing_call
):
    with client_factory() as client:
        session_id = _ready_session(client, long_pdf)
        _start(client, session_id)
        gemini.fail_next(failing_call, error)  # scoring the answer, or asking the next question after it
        busy = _answer(client, session_id)
        assert busy.status_code == 503
        assert busy.json()["detail"] == PANEL_BUSY_MESSAGE and busy.headers["retry-after"] == "60"
        defense = client.app.state.store.get(session_id).defense
        assert defense.session.turns == [] and defense.pending.question == "Question 1?"  # not recorded

        retried = _answer(client, session_id)
        assert retried.status_code == 200 and retried.json()["next_question"]["turn"] == 2
        assert len(client.app.state.store.get(session_id).defense.session.turns) == 1
    assert quota_store.used(TODAY) == 1  # daily_usage untouched by the busy turn


def test_another_gemini_error_is_502_and_records_nothing(client_factory, gemini, long_pdf):
    with client_factory() as client:
        session_id = _ready_session(client, long_pdf)
        _start(client, session_id)
        gemini.fail_next("scoring", LLMProviderError("malformed JSON, retried once"))
        failed = _answer(client, session_id)
        assert (failed.status_code, failed.json()["detail"]) == (502, PANEL_ERROR_MESSAGE)
        assert _answer(client, session_id).status_code == 200


def test_a_failed_first_question_resumes_on_start_without_a_second_reservation(
    client_factory, gemini, quota_store, long_pdf
):
    gemini.fail_next("question", LLMQuotaExhaustedError("429", model="gemini-3.1-flash-lite"))
    with client_factory() as client:
        session_id = _ready_session(client, long_pdf)
        busy = _start(client, session_id)
        assert busy.status_code == 503
        assert _answer(client, session_id).json()["detail"] == NOT_STARTED_MESSAGE
        resumed = _start(client, session_id)
    assert resumed.status_code == 200 and resumed.json()["question"]["turn"] == 1
    assert quota_store.used(TODAY) == 1
    assert gemini.calls_by_role["persona"] == 1  # the panel wasn't generated again


def test_a_report_call_failing_still_finishes_with_v1s_fallbacks(client_factory, gemini, long_pdf):
    gemini.fail_next("narrative", LLMQuotaExhaustedError("429", model="gemini-3.1-flash-lite"))
    gemini.fail_next("suggestions", LLMQuotaExhaustedError("429", model="gemini-2.5-flash"))
    with client_factory() as client:
        session_id = _ready_session(client, long_pdf)
        _start(client, session_id)
        assert _run_to_end(client, session_id)[-1]["done"] is True
        report = client.get(f"/sessions/{session_id}/report").json()["report"]
    assert report["narrative"] is None and report["narrative_fallback_used"] is True
    assert report["answer_suggestions"] == [] and report["suggestions_fallback_used"] is True


def test_the_server_refuses_to_start_without_a_gemini_key(tmp_path, embedder, quota_store):
    from fastapi.testclient import TestClient

    from backend.app import create_app
    from backend.tests.conftest import make_settings

    app = create_app(
        make_settings(upload_dir=tmp_path / "uploads"), embedder_factory=lambda _s: embedder,
        quota_store_factory=lambda _s: quota_store,
    )
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY is not set"):
        with TestClient(app):
            pass


def test_the_gemini_key_is_never_logged_or_shown(monkeypatch, caplog):
    import logging
    from unittest.mock import patch

    from backend.defense_routes import gemini_providers_factory
    from backend.tests.conftest import make_settings
    from academic_defense_simulator.llm.provider import CallCounter

    key = "AIza-not-a-real-key-123"
    settings = make_settings(GEMINI_API_KEY=key)
    with caplog.at_level(logging.DEBUG), patch("google.genai.Client") as client_cls:
        providers = gemini_providers_factory(settings)(CallCounter())
    assert client_cls.call_args.kwargs["api_key"] == key  # it does reach the client
    assert key not in caplog.text and key not in repr(settings) and key not in repr(providers)
    assert key not in settings.model_dump_json()
