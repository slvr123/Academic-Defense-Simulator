"""backend/quota.py without a database: the cap plan, the Pacific day, the
in-memory store's reserve, and the startup warm-up."""

from __future__ import annotations

import logging
import threading
from datetime import date, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from backend.quota import (
    MAX_PANEL_SIZE,
    InMemoryQuotaStore,
    QuotaUnavailable,
    log_plan,
    pacific_day,
    plan_quota,
    resets_at,
    warm_up,
)
from backend.tests.conftest import make_settings

UTC = timezone.utc


# --- the cap ---


def test_default_plan_is_bound_by_the_defense_model():
    plan = plan_quota(make_settings())
    shares = {s.model: (s.calls_per_session, s.rpd, s.sessions) for s in plan.shares}
    assert shares == {"gemini-3.1-flash-lite": (40, 500, 12), "gemini-2.5-flash": (1, 20, 20)}
    assert plan.binding.model == "gemini-3.1-flash-lite"
    assert plan.cap == 11  # floor(500 / 40) - margin 1


def test_the_suggestions_model_binds_when_its_rpd_is_lower():
    limits = {"gemini-3.1-flash-lite": {"rpd": 500, "rpm": 15}, "gemini-2.5-flash": {"rpd": 5, "rpm": 5}}
    plan = plan_quota(make_settings(gemini_limits=limits, quota_margin=0))
    assert plan.binding.model == "gemini-2.5-flash" and plan.cap == 5


def test_one_model_for_everything_adds_its_calls_together():
    plan = plan_quota(make_settings(gemini_suggestions_model="gemini-3.1-flash-lite"))
    [share] = plan.shares
    assert (share.calls_per_session, plan.cap) == (41, 500 // 41 - 1)


def test_cap_never_goes_below_zero_and_warns_when_low(caplog):
    plan = plan_quota(make_settings(gemini_model="gemini-2.5-flash", quota_margin=5))
    assert plan.cap == 0
    with caplog.at_level(logging.INFO, logger="backend.quota"):
        log_plan(plan)
    lines = [r.getMessage() for r in caplog.records]
    assert "quota daily_cap=0 binding_model=gemini-2.5-flash margin=5" in lines
    assert any(r.levelno == logging.WARNING and "QUOTA DAILY CAP IS 0" in r.getMessage() for r in caplog.records)


def test_the_full_panel_is_three_domain_panelists_plus_devils_advocate():
    assert MAX_PANEL_SIZE == 4


def test_a_model_in_use_without_limits_fails_at_startup():
    with pytest.raises(ValidationError, match="no rpd/rpm for \\['gemini-9'\\]"):
        make_settings(gemini_model="gemini-9")


def test_limits_come_from_json_in_the_environment(monkeypatch):
    monkeypatch.setenv(
        "ADS_GEMINI_LIMITS",
        '{"gemini-3.1-flash-lite": {"rpd": 1000, "rpm": 30}, "gemini-2.5-flash": {"rpd": 250, "rpm": 10}}',
    )
    settings = make_settings()
    assert settings.gemini_limits["gemini-3.1-flash-lite"].rpd == 1000
    assert plan_quota(settings).cap == 1000 // 40 - 1


def test_rpm_ceiling_is_the_limit_less_the_verified_margin():
    settings = make_settings()
    assert settings.rpm_ceiling("gemini-3.1-flash-lite") == 13  # v1's verified-clean value at 15 RPM
    assert settings.rpm_ceiling("gemini-2.5-flash") == 3


# --- the Pacific day ---


@pytest.mark.parametrize(
    "utc, expected",
    [
        (datetime(2026, 10, 21, 6, 59, 59, tzinfo=UTC), date(2026, 10, 20)),  # 23:59:59 PDT
        (datetime(2026, 10, 21, 7, 0, 0, tzinfo=UTC), date(2026, 10, 21)),  # midnight PDT
        (datetime(2026, 12, 1, 7, 59, 59, tzinfo=UTC), date(2026, 11, 30)),  # 23:59:59 PST
        (datetime(2026, 12, 1, 8, 0, 0, tzinfo=UTC), date(2026, 12, 1)),  # midnight PST
    ],
)
def test_the_day_rolls_over_at_midnight_pacific(utc, expected):
    assert pacific_day(utc) == expected


def test_resets_at_follows_daylight_saving():
    assert resets_at(date(2026, 10, 20)).astimezone(UTC) == datetime(2026, 10, 21, 7, tzinfo=UTC)
    assert resets_at(date(2026, 12, 1)).astimezone(UTC) == datetime(2026, 12, 2, 8, tzinfo=UTC)
    # 2026-11-01 is the fall-back day: the night that ends it is already on PST.
    assert resets_at(date(2026, 10, 31)).astimezone(UTC) == datetime(2026, 11, 1, 7, tzinfo=UTC)
    assert resets_at(date(2026, 11, 1)).astimezone(UTC) == datetime(2026, 11, 2, 8, tzinfo=UTC)


# --- the in-memory store ---


def test_reserve_stops_exactly_at_the_cap():
    store, day = InMemoryQuotaStore(), date(2026, 10, 20)
    assert [store.reserve(day, 2) for _ in range(3)] == [1, 2, None]
    assert store.used(day) == 2
    assert store.used(day + timedelta(days=1)) == 0  # a new day starts empty
    assert store.reserve(day + timedelta(days=1), 2) == 1


def test_concurrent_reserves_for_the_last_slot_have_one_winner():
    store, day = InMemoryQuotaStore(), date(2026, 10, 20)
    store.reserve(day, 5)
    barrier = threading.Barrier(8)
    results: list[int | None] = []

    def start() -> None:
        barrier.wait()
        results.append(store.reserve(day, 2))

    threads = [threading.Thread(target=start) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results, key=lambda r: r is None) == [2] + [None] * 7


# --- startup warm-up ---


class FlakyStore:
    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.attempts = 0

    def ensure_schema(self) -> None:
        self.attempts += 1
        if self.attempts <= self.failures:
            raise QuotaUnavailable("connection refused")


def test_warm_up_retries_while_the_database_wakes():
    sleeps: list[float] = []
    store = FlakyStore(failures=2)
    assert warm_up(store, sleep=sleeps.append) is True
    assert store.attempts == 3 and sleeps == [1.0, 2.0]


def test_warm_up_gives_up_without_raising(caplog):
    sleeps: list[float] = []
    store = FlakyStore(failures=99)
    with caplog.at_level(logging.WARNING, logger="backend.quota"):
        assert warm_up(store, sleep=sleeps.append) is False
    assert store.attempts == 5 and sleeps == [1.0, 2.0, 4.0, 8.0]
    assert "will retry on first use" in caplog.records[-1].getMessage()
