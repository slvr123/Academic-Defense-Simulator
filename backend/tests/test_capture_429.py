"""scripts/capture_429.py against a fake genai client: the burst and daily loops,
the probe that labels each 429, and key redaction. No network."""

from __future__ import annotations

import threading

import pytest
from google.genai import errors as genai_errors

from scripts import capture_429
from scripts.capture_429 import Capture, redact, run_burst, run_daily

KEY = "AIza-test-secret"


def _429(scope: str) -> genai_errors.ClientError:
    return genai_errors.ClientError(
        429,
        {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": f"quota {scope} for key {KEY}",
                   "details": [{"quotaId": scope}]}},
    )


class FakeModels:
    """Succeeds `ok_before` times, then raises from `errors` in turn (the last repeats)."""

    def __init__(self, ok_before: int, errors: list[Exception]) -> None:
        self.ok_before = ok_before
        self.errors = errors
        self.calls = 0
        self._lock = threading.Lock()

    def generate_content(self, **_kwargs):
        with self._lock:
            self.calls += 1
            n = self.calls
        if n <= self.ok_before:
            return object()
        return_error = self.errors[min(n - self.ok_before - 1, len(self.errors) - 1)]
        if return_error is None:
            return object()
        raise return_error


class FakeClient:
    def __init__(self, models: FakeModels) -> None:
        self.models = models


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(capture_429.time, "sleep", lambda _s: None)


def test_redact_reaches_nested_strings():
    assert redact({"a": [f"x{KEY}y", {"b": KEY}], "n": 3}, KEY) == {"a": ["x<redacted>y", {"b": "<redacted>"}], "n": 3}


def test_burst_saves_the_first_429_and_labels_it_per_minute_when_the_probe_succeeds():
    models = FakeModels(ok_before=15, errors=[_429("PerMinute"), None])
    capture = Capture(FakeClient(models), "m", KEY)
    # 1 worker keeps the fake's order deterministic: 15 ok, the 429, then the probe.
    run_burst(capture, workers=1, max_calls=60, probe_wait=0)
    assert capture.successes == 16  # 15 before the 429, plus the probe
    [record] = capture.records
    assert record["code"] == 429 and record["status"] == "RESOURCE_EXHAUSTED"
    assert record["details"]["error"]["details"] == [{"quotaId": "PerMinute"}]
    assert record["probe_after_wait"]["outcome"] == "ok"
    assert KEY not in str(record) and "<redacted>" in record["message"]


def test_daily_continues_past_a_per_minute_429_and_stops_when_the_probe_429s_too():
    models = FakeModels(ok_before=3, errors=[_429("PerMinute"), None, None, _429("PerDay")])
    capture = Capture(FakeClient(models), "m", KEY)
    run_daily(capture, interval=0, max_calls=40, probe_wait=0)
    outcomes = [r.get("probe_after_wait", {}).get("outcome") for r in capture.records]
    assert outcomes == ["ok", "429", None]  # minute 429, day 429, then the probe's own 429
    assert capture.records[2]["note"] == "the probe request after the wait"
    assert capture.records[1]["details"]["error"]["details"] == [{"quotaId": "PerDay"}]


def test_a_non_429_error_is_raised_for_main_to_save():
    models = FakeModels(ok_before=0, errors=[genai_errors.ClientError(400, {"error": {"code": 400}})])
    with pytest.raises(genai_errors.APIError):
        run_daily(Capture(FakeClient(models), "m", KEY), interval=0, max_calls=5, probe_wait=0)
