"""Demo-mode daily counter tests (v0.4a Brief step 7). Pure filesystem logic,
zero LLM calls — every test uses tmp_path so no test touches the real counter
file or any other test's state.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

from academic_defense_simulator.demo_counter import (
    DEMO_DAILY_SESSION_CAP,
    consume_demo_session,
    demo_available,
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
    for _ in range(DEMO_DAILY_SESSION_CAP):
        consume_demo_session(path)
    assert demo_sessions_used_today(path) == DEMO_DAILY_SESSION_CAP
    assert demo_available(path) is False


def test_below_cap_still_available(tmp_path):
    path = tmp_path / "counter.json"
    for _ in range(DEMO_DAILY_SESSION_CAP - 1):
        consume_demo_session(path)
    assert demo_available(path) is True


def test_date_rollover_resets_count(tmp_path):
    path = tmp_path / "counter.json"
    stale_day = (date.today() - timedelta(days=1)).isoformat()
    path.write_text(json.dumps({"date": stale_day, "count": DEMO_DAILY_SESSION_CAP}), encoding="utf-8")

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
