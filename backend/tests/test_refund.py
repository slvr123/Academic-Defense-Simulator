"""M11 P1: a slot is refunded when a defense never got its first question.

A session holds at most one charge. It's released once, when setup fails (a), or when
the session is deleted or expires before a first question was delivered (b). Once a
first question is delivered the charge is final. Faults are injected with monkeypatch
(setup_defense) and the fake Gemini's fail_next (a busy first question).
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import pytest

from academic_defense_simulator.llm.provider import LLMServiceUnavailableError
from backend import defense_routes
from backend.quota import InMemoryQuotaStore
from backend.tests.conftest import make_pdf, upload, wait_for

TODAY = date(2026, 10, 20)  # the FakeClock's Pacific day


@pytest.fixture
def long_pdf(tmp_path):
    return make_pdf(tmp_path / "Library System.pdf", pages=16)


def _ready(client, pdf) -> str:
    session_id = upload(client, pdf).json()["session_id"]
    wait_for(client, session_id, lambda s: s.get("status") == "ready")
    return session_id


def _busy(gemini) -> None:
    """The first question comes back overloaded: Gemini's 503 after the provider's own retry."""
    gemini.fail_next("question", LLMServiceUnavailableError("503 UNAVAILABLE", model="gemini-3.1-flash-lite"))


def _starts_left(client) -> int:
    return client.get("/quota").json()["remaining"]


def _releases(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("quota release")]


def test_setup_failure_releases_at_once_and_a_retry_charges_once(client_factory, quota_store, long_pdf, monkeypatch, caplog):
    """(a). Before M11 a failing setup_defense left the slot taken and the retry took a second one."""
    real = defense_routes.setup_defense
    failures = iter([RuntimeError("injected setup failure")])

    def flaky(*args, **kwargs):
        error = next(failures, None)
        if error is not None:
            raise error
        return real(*args, **kwargs)

    monkeypatch.setattr(defense_routes, "setup_defense", flaky)
    caplog.set_level(logging.INFO, logger="backend.refund")
    with client_factory() as client:
        client.app.router.raise_server_exceptions = False
        session_id = _ready(client, long_pdf)
        with pytest.raises(RuntimeError):
            client.post(f"/sessions/{session_id}/start")
        assert quota_store.used(TODAY) == 0  # released at once
        assert client.post(f"/sessions/{session_id}/start").status_code == 200
    assert quota_store.used(TODAY) == 1  # one charge in all
    assert len(_releases(caplog)) == 1 and "reason=setup_failed" in _releases(caplog)[0]


def test_a_busy_first_question_then_delete_releases(client_factory, gemini, quota_store, long_pdf, caplog):
    caplog.set_level(logging.INFO, logger="backend.refund")
    with client_factory() as client:
        session_id = _ready(client, long_pdf)
        _busy(gemini)
        busy = client.post(f"/sessions/{session_id}/start")
        assert busy.status_code == 503 and "Retry-After" in busy.headers
        assert busy.json()["code"] == "panel_busy"  # additive: tells it from the quota store's 503
        assert quota_store.used(TODAY) == 1  # held while the session lives: the retry is free
        assert client.delete(f"/sessions/{session_id}").status_code == 204
        assert quota_store.used(TODAY) == 0
        assert _starts_left(client) == client.get("/quota").json()["cap"]
    assert any("reason=deleted" in line for line in _releases(caplog))


def test_a_busy_first_question_then_a_successful_retry_neither_releases_nor_charges_twice(client_factory, gemini, quota_store, long_pdf, caplog):
    caplog.set_level(logging.INFO, logger="backend.refund")
    with client_factory() as client:
        session_id = _ready(client, long_pdf)
        _busy(gemini)
        assert client.post(f"/sessions/{session_id}/start").status_code == 503
        assert client.post(f"/sessions/{session_id}/start").status_code == 200
        assert quota_store.used(TODAY) == 1
        assert client.delete(f"/sessions/{session_id}").status_code == 204
    assert quota_store.used(TODAY) == 1  # a delivered first question is final
    assert _releases(caplog) == []


def test_a_delivered_first_question_then_delete_does_not_release(client_factory, quota_store, long_pdf, caplog):
    caplog.set_level(logging.INFO, logger="backend.refund")
    with client_factory() as client:
        session_id = _ready(client, long_pdf)
        assert client.post(f"/sessions/{session_id}/start").status_code == 200
        assert client.delete(f"/sessions/{session_id}").status_code == 204
    assert quota_store.used(TODAY) == 1
    assert _releases(caplog) == []


def test_a_double_delete_releases_once(client_factory, gemini, quota_store, long_pdf, caplog):
    caplog.set_level(logging.INFO, logger="backend.refund")
    with client_factory() as client:
        session_id = _ready(client, long_pdf)
        other = _ready(client, make_pdf(long_pdf.parent / "Other.pdf", pages=16))
        assert client.post(f"/sessions/{other}/start").status_code == 200  # a second, final charge
        _busy(gemini)
        assert client.post(f"/sessions/{session_id}/start").status_code == 503
        assert quota_store.used(TODAY) == 2
        assert client.delete(f"/sessions/{session_id}").status_code == 204
        assert client.delete(f"/sessions/{session_id}").status_code == 404
    assert quota_store.used(TODAY) == 1
    assert len(_releases(caplog)) == 1


def test_an_expired_session_that_never_got_a_question_releases(client_factory, gemini, quota_store, clock, long_pdf, caplog):
    caplog.set_level(logging.INFO, logger="backend.refund")
    with client_factory(session_ttl_seconds=60) as client:
        session_id = _ready(client, long_pdf)
        _busy(gemini)
        assert client.post(f"/sessions/{session_id}/start").status_code == 503
        store = client.app.state.store
        store._clock = lambda: 10**9  # far past the TTL
        store.sweep()
    assert quota_store.used(TODAY) == 0
    assert any("reason=expired" in line for line in _releases(caplog))


def test_a_release_on_a_new_pacific_day_skips_the_daily_store(client_factory, gemini, quota_store, clock, long_pdf, caplog):
    caplog.set_level(logging.INFO, logger="backend.refund")
    with client_factory() as client:
        session_id = _ready(client, long_pdf)
        _busy(gemini)
        assert client.post(f"/sessions/{session_id}/start").status_code == 503
        clock.now += timedelta(days=1)
        assert client.delete(f"/sessions/{session_id}").status_code == 204
    assert quota_store.used(TODAY) == 1  # yesterday's count stays as it was
    [line] = _releases(caplog)
    assert "daily release skipped" in line and "2026-10-20" in line and "2026-10-21" in line


def test_the_memory_store_release_never_goes_below_zero():
    store = InMemoryQuotaStore()
    assert store.release(TODAY) == 0
    assert store.reserve(TODAY, 3) == 1
    assert store.release(TODAY) == 0
    assert store.release(TODAY) == 0
    assert store.used(TODAY) == 0
