"""Demo-mode daily counter tests (v0.4a Brief step 7; env-override amendment adds
the ADS_DEMO_* cases below). Pure filesystem/env-var logic, zero LLM calls —
every test uses tmp_path so no test touches the real counter file or any other
test's state.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from academic_defense_simulator.demo_counter import (
    GrantPlan,
    GrantWrite,
    consume_demo_session,
    demo_available,
    demo_daily_session_cap,
    demo_max_turns,
    demo_sessions_per_browser,
    demo_sessions_used_today,
    plan_demo_grant,
)


def test_missing_file_reads_as_zero(tmp_path):
    path = tmp_path / "counter.json"
    assert demo_sessions_used_today(path) == 0
    assert demo_available(path) is True


def test_consume_increments_and_persists(tmp_path):
    path = tmp_path / "counter.json"
    assert consume_demo_session(path) == 1
    assert consume_demo_session(path) == 2
    assert demo_sessions_used_today(path) == 2


def test_cap_enforced_at_boundary(tmp_path):
    path = tmp_path / "counter.json"
    cap = demo_daily_session_cap()
    for _ in range(cap):
        consume_demo_session(path)
    assert demo_sessions_used_today(path) == cap
    assert demo_available(path) is False


def test_below_cap_still_available(tmp_path):
    path = tmp_path / "counter.json"
    for _ in range(demo_daily_session_cap() - 1):
        consume_demo_session(path)
    assert demo_available(path) is True


def test_date_rollover_resets_count(tmp_path):
    path = tmp_path / "counter.json"
    stale_day = (date.today() - timedelta(days=1)).isoformat()
    path.write_text(json.dumps({"date": stale_day, "count": demo_daily_session_cap()}), encoding="utf-8")

    # A stale date's count must not carry over into today's read...
    assert demo_sessions_used_today(path) == 0
    assert demo_available(path) is True

    # ...and a consume from a stale day starts today's count at 1, not
    # incrementing yesterday's number forward.
    assert consume_demo_session(path) == 1
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written == {"date": date.today().isoformat(), "count": 1}


def test_corrupt_file_treated_as_empty_day(tmp_path):
    path = tmp_path / "counter.json"
    path.write_text("not json", encoding="utf-8")
    assert demo_sessions_used_today(path) == 0
    assert consume_demo_session(path) == 1


# --- env-override amendment: ADS_DEMO_MAX_TURNS / ADS_DEMO_SESSIONS_PER_BROWSER /
# ADS_DEMO_DAILY_CAP, each defaulting to its locked value (4, 1, 8) when unset. ---


def test_demo_max_turns_defaults_when_unset(monkeypatch):
    monkeypatch.delenv("ADS_DEMO_MAX_TURNS", raising=False)
    assert demo_max_turns() == 4


def test_demo_max_turns_overridden(monkeypatch):
    monkeypatch.setenv("ADS_DEMO_MAX_TURNS", "12")
    assert demo_max_turns() == 12


def test_demo_sessions_per_browser_defaults_when_unset(monkeypatch):
    monkeypatch.delenv("ADS_DEMO_SESSIONS_PER_BROWSER", raising=False)
    assert demo_sessions_per_browser() == 1


def test_demo_sessions_per_browser_overridden(monkeypatch):
    monkeypatch.setenv("ADS_DEMO_SESSIONS_PER_BROWSER", "2")
    assert demo_sessions_per_browser() == 2


def test_demo_daily_session_cap_defaults_when_unset(monkeypatch):
    monkeypatch.delenv("ADS_DEMO_DAILY_CAP", raising=False)
    assert demo_daily_session_cap() == 8


def test_demo_daily_session_cap_overridden(monkeypatch):
    monkeypatch.setenv("ADS_DEMO_DAILY_CAP", "16")
    assert demo_daily_session_cap() == 16


def test_demo_available_uses_overridden_cap_by_default(monkeypatch, tmp_path):
    monkeypatch.setenv("ADS_DEMO_DAILY_CAP", "2")
    path = tmp_path / "counter.json"
    consume_demo_session(path)
    assert demo_available(path) is True
    consume_demo_session(path)
    assert demo_available(path) is False


def test_demo_env_vars_ignore_blank_and_malformed_values(monkeypatch):
    monkeypatch.setenv("ADS_DEMO_MAX_TURNS", "")
    assert demo_max_turns() == 4
    monkeypatch.setenv("ADS_DEMO_MAX_TURNS", "not-a-number")
    assert demo_max_turns() == 4


# --- v1.3a Decision 2: demo grant, stop point by stop point ---
#
# A driver run can be stopped between any two writes of a grant. Each case stops
# the first run after `stop_after` writes, then retries with a fresh plan derived
# from whatever state was left, exactly as the next click would.


def _plan(state):
    return plan_demo_grant(
        has_key=state["key"] is not None,
        mode=state["mode"],
        browser_sessions_started=state["browser"],
        per_browser_limit=1,
        daily_available=state["daily"] < 10,
    )


def _apply(state, writes):
    for write in writes:
        if write is GrantWrite.MARK_DEMO:
            state["mode"] = "demo"
        elif write is GrantWrite.COUNT_BROWSER:
            state["browser"] += 1
        elif write is GrantWrite.SET_KEY:
            state["key"] = "project-key"
        elif write is GrantWrite.COUNT_DAILY:
            state["daily"] += 1


@pytest.mark.parametrize(
    ("stop_after", "retry_writes", "browser", "daily"),
    [
        (0, ("mark_demo", "count_browser", "set_key", "count_daily"), 1, 1),  # stopped before MARK_DEMO
        (1, ("set_key", "count_daily"), 0, 1),  # before COUNT_BROWSER: browser one low
        (2, ("set_key", "count_daily"), 1, 1),  # before SET_KEY
        (3, (), 1, 0),  # before COUNT_DAILY: daily one low
        (4, (), 1, 1),  # after the last write: complete
    ],
)
def test_a_stopped_grant_never_counts_twice(stop_after, retry_writes, browser, daily):
    state = {"key": None, "mode": None, "browser": 0, "daily": 0}
    first = _plan(state)
    assert first.writes == (
        GrantWrite.MARK_DEMO, GrantWrite.COUNT_BROWSER, GrantWrite.SET_KEY, GrantWrite.COUNT_DAILY,
    )
    _apply(state, first.writes[:stop_after])

    retry = _plan(state)
    assert tuple(w.value for w in retry.writes) == retry_writes
    _apply(state, retry.writes)

    assert retry.allowed is True
    assert state["key"] == "project-key"
    assert (state["browser"], state["daily"]) == (browser, daily)
    # A third click after the grant completed changes nothing.
    assert _plan(state) == GrantPlan(allowed=True)


def test_a_fresh_grant_is_refused_at_either_cap():
    common = {"has_key": False, "mode": None}
    assert plan_demo_grant(**common, browser_sessions_started=1, per_browser_limit=1, daily_available=True) == GrantPlan(
        allowed=False
    )
    assert plan_demo_grant(**common, browser_sessions_started=0, per_browser_limit=1, daily_available=False) == GrantPlan(
        allowed=False
    )


def test_an_own_key_never_spends_a_slot():
    plan = plan_demo_grant(
        has_key=True, mode="own", browser_sessions_started=0, per_browser_limit=1, daily_available=True
    )
    assert plan == GrantPlan(allowed=True)
