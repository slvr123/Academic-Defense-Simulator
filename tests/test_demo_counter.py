"""Demo-mode daily counter tests (v0.4a Brief step 7; env-override amendment adds
the ADS_DEMO_* cases below). Pure filesystem/env-var logic, zero LLM calls —
every test uses tmp_path so no test touches the real counter file or any other
test's state.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

from academic_defense_simulator.demo_counter import (
    consume_demo_session,
    demo_available,
    demo_daily_session_cap,
    demo_max_turns,
    demo_sessions_per_browser,
    demo_sessions_used_today,
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
