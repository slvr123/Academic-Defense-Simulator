"""scripts/measure_ingest.py against a fake backend: restart detection, gates, doc writing.

The fake server is an httpx.MockTransport that answers /health, /sessions and
/debug/memory like the real backend, and can "restart" (new start time, sessions
gone) at a chosen moment, which is what an out-of-memory kill looks like from outside.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from backend.tests.conftest import make_pdf
from scripts.measure_ingest import (
    DOC_END,
    DOC_START,
    Identity,
    RunResult,
    evaluate,
    measure_run,
    peak_phase,
    phase_table,
    restart_detected,
    results_markdown,
    write_results,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class FakeBackend:
    """`restart_on_poll=n` restarts the process when the n-th status poll arrives.
    `restart_after_ready` restarts right after the session reports ready.
    `spin_down` restarts the process whenever the client sleeps a long gap."""

    def __init__(self, clock: FakeClock, polls_to_ready: int = 3, hwm_mb: float = 370.0) -> None:
        self.clock = clock
        self.polls_to_ready = polls_to_ready
        self.hwm_mb = hwm_mb
        self.pid = 7
        self.started_at = 1000.0
        self.sessions: dict[str, int] = {}
        self.last_job: dict | None = None
        self.restart_on_poll: int | None = None
        self.restart_after_ready = False
        self.cold_start_polls = 0  # /health answers 502 this many times after a restart
        self._health_misses = 0
        self._polls = 0

    def restart(self) -> None:
        self.started_at += 1.0
        self.sessions.clear()
        self.last_job = None
        self._health_misses = self.cold_start_polls

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/health":
            if self._health_misses:
                self._health_misses -= 1
                return httpx.Response(502)
            return httpx.Response(200, json={"status": "ok", "model_loaded": True})
        if path == "/debug/memory":
            return httpx.Response(200, json={
                "pid": self.pid, "process_started_at": self.started_at, "commit": "abc1234",
                "max_upload_mb": 30, "max_pages": 250, "rss_mb": 300.0, "hwm_mb": self.hwm_mb,
                "last_job": self.last_job,
            })
        if path == "/sessions" and request.method == "POST":
            session_id = f"s{len(self.sessions) + 1}-{self.started_at}"
            self.sessions[session_id] = 0
            return httpx.Response(202, json={"session_id": session_id, "status": "queued", "queue_position": 1})
        session_id = path.removeprefix("/sessions/")
        if request.method == "DELETE":
            return httpx.Response(204 if self.sessions.pop(session_id, None) is not None else 404)
        self._polls += 1
        if self.restart_on_poll == self._polls:
            self.restart()
        if session_id not in self.sessions:
            return httpx.Response(404, json={"detail": "Session not found."})
        self.sessions[session_id] += 1
        if self.sessions[session_id] < self.polls_to_ready:
            return httpx.Response(200, json={"status": "embedding", "progress": 0.5})
        self.last_job = {
            "session_id": session_id, "outcome": "ready", "pages": 224, "chunks": 92,
            "job_peak_rss_mb": 306.2, "parse_s": 0.4, "embed_s": 55.0, "total_s": 55.5,
            "phases": [
                {"phase": "spooled", "rss_mb": 285.0, "hwm_mb": 286.5},
                {"phase": "parsed", "rss_mb": 300.0, "hwm_mb": 302.0},
                {"phase": "released", "rss_mb": 290.0, "hwm_mb": 302.0},
                {"phase": "embedded", "rss_mb": 360.0, "hwm_mb": self.hwm_mb},
                {"phase": "job_end", "rss_mb": 330.0, "hwm_mb": self.hwm_mb},
            ],
        }
        if self.restart_after_ready:
            self.restart_after_ready = False
            body = {"status": "ready", "progress": 1.0, "chunk_count": 92}
            self.restart()
            return httpx.Response(200, json=body)
        return httpx.Response(200, json={"status": "ready", "progress": 1.0, "chunk_count": 92})


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def backend(clock: FakeClock) -> FakeBackend:
    return FakeBackend(clock)


@pytest.fixture
def pdf(tmp_path: Path) -> Path:
    return make_pdf(tmp_path / "thesis.pdf")


def _client(backend: FakeBackend) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(backend.handler), base_url="http://fake")


def _run(backend, clock, pdf, run=1, previous=None, gap_minutes=16.0) -> RunResult:
    def sleep(seconds: float) -> None:
        if seconds >= 60:  # the spin-down gap: the free instance stops and the next request boots it
            backend.restart()
        clock.sleep(seconds)

    with _client(backend) as client:
        return measure_run(
            client, run, pdf, gap_minutes=gap_minutes, previous=previous,
            sleep=sleep, clock=clock, log=lambda _line: None,
        )


# --- restart_detected ---


def test_same_process_and_session_present_is_not_a_restart():
    same = Identity(pid=7, started_at=1000.0)
    assert not restart_detected(same, Identity(pid=7, started_at=1000.0), session_lost=False)


@pytest.mark.parametrize(
    "after, session_lost",
    [
        (Identity(pid=8, started_at=1000.0), False),  # new pid
        (Identity(pid=7, started_at=1001.0), False),  # same pid (containers reuse them), new start time
        (Identity(pid=7, started_at=1000.0), True),  # session 404'd
        (None, False),  # /debug/memory never answered again
    ],
)
def test_restart_signals(after, session_lost):
    assert restart_detected(Identity(pid=7, started_at=1000.0), after, session_lost)


# --- measure_run against the fake server ---


def test_clean_run_records_timings_memory_and_job_stats(backend, clock, pdf):
    result = _run(backend, clock, pdf)
    assert result.status == "ready" and not result.restarted
    assert result.waited_min == 0 and result.fresh_process is None
    assert result.hwm_after_mb == 370.0
    assert result.chunks == 92 and result.job["embed_s"] == 55.0
    assert result.upload_to_ready_s == pytest.approx(1.0)  # two 0.5 s polls before ready
    table = phase_table([result])
    assert "| 1 | 285.0 / 286.5 | 300.0 / 302.0 | 290.0 / 302.0 | 360.0 / 370.0 | 330.0 / 370.0 | embedded |" in table
    assert backend.sessions == {}  # the script deletes its session


def test_restart_during_ingest_is_flagged_likely_oom(backend, clock, pdf):
    backend.restart_on_poll = 2
    result = _run(backend, clock, pdf)
    assert result.status == "lost"
    assert result.restarted and result.note == "process restarted, likely OOM"
    assert result.hwm_after_mb is None  # the new process's VmHWM says nothing about the run


def test_restart_after_ready_is_caught_by_the_identity_check(backend, clock, pdf):
    backend.restart_after_ready = True
    result = _run(backend, clock, pdf)
    assert result.status == "ready"
    assert result.restarted and result.hwm_after_mb is None


def test_gap_spins_down_and_cold_start_counts_502s(backend, clock, pdf):
    first = _run(backend, clock, pdf)
    backend.cold_start_polls = 30
    second = _run(backend, clock, pdf, run=2, previous=first)
    assert second.waited_min == 16 and second.fresh_process is True
    assert second.cold_start_s == pytest.approx(30.0)  # 30 retries, 1 s apart
    assert not second.restarted


def test_instance_that_never_spun_down_is_not_fresh(backend, clock, pdf):
    first = _run(backend, clock, pdf)
    with _client(backend) as client:
        second = measure_run(
            client, 2, pdf, gap_minutes=16, previous=first,
            sleep=clock.sleep, clock=clock, log=lambda _line: None,  # sleeping doesn't restart it
        )
    assert second.fresh_process is False and not second.restarted


# --- gates ---


def _ready(run: int, **overrides) -> RunResult:
    values = dict(
        run=run, waited_min=0 if run == 1 else 16, cold_start_s=40.0, fresh_process=None if run == 1 else True,
        status="ready", upload_to_ready_s=70.0, chunks=92, hwm_after_mb=380.0,
    )
    values.update(overrides)
    return RunResult(**values)


def test_all_gates_pass_is_go():
    verdict, reasons = evaluate([_ready(1), _ready(2), _ready(3)])
    assert verdict == "GO", reasons


@pytest.mark.parametrize(
    "bad, expected",
    [
        (dict(restarted=True, status="lost", hwm_after_mb=None), "process restarted, likely OOM"),
        (dict(hwm_after_mb=401.0), "VmHWM 401.0 MB > 400 MB"),
        (dict(cold_start_s=91.0), "cold start 91.0 s > 90 s"),
        (dict(upload_to_ready_s=121.0), "upload-to-ready 121.0 s > 120 s"),
    ],
)
def test_any_failed_gate_is_no_go(bad, expected):
    verdict, reasons = evaluate([_ready(1), _ready(2, **bad), _ready(3)])
    assert verdict == "NO-GO"
    assert any(expected in reason for reason in reasons), reasons


def test_run_one_cold_start_is_not_gated():
    assert evaluate([_ready(1, cold_start_s=200.0), _ready(2), _ready(3)])[0] == "GO"


def test_no_spin_down_is_inconclusive_not_go():
    verdict, reasons = evaluate([_ready(1), _ready(2, fresh_process=False), _ready(3)])
    assert verdict == "INCONCLUSIVE" and "never spun down" in reasons[0]


def test_single_run_cannot_measure_cold_start():
    verdict, reasons = evaluate([_ready(1)])
    assert verdict == "INCONCLUSIVE" and "cold start wasn't measured" in reasons[0]


# --- results doc ---


def test_results_replace_only_the_measured_section(tmp_path, pdf):
    doc = tmp_path / "results.md"
    doc.write_text(f"# Title\n\nkept above\n\n{DOC_START}\nSTALE RESULTS\n{DOC_END}\n\nkept below\n", encoding="utf-8")
    runs = [_ready(1), _ready(2), _ready(3)]
    verdict, reasons = evaluate(runs)
    debug = {"commit": "abc1234", "max_upload_mb": 30, "max_pages": 250}
    block = results_markdown(
        runs, verdict, reasons, pdf=pdf, size_mb=26.43, pages=224, base_url="https://svc",
        debug=debug, finished=datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc),
    )
    write_results(doc, block)
    write_results(doc, block)  # rerunning replaces, doesn't append

    text = doc.read_text(encoding="utf-8")
    assert text.startswith("# Title\n\nkept above\n\n") and text.endswith("\n\nkept below\n")
    assert "STALE RESULTS" not in text and text.count(DOC_START) == 1
    assert "Verdict: **GO**" in text and "`abc1234`" in text and "`ADS_MAX_PAGES` = 250" in text


# --- phase breakdown ---


def _phases(*highs):
    names = ("spooled", "parsed", "released", "embedded", "job_end")
    return {n: {"phase": n, "rss_mb": 1.0, "hwm_mb": h} for n, h in zip(names, highs)}


@pytest.mark.parametrize(
    "highs, expected",
    [
        ((286.0, 290.0, 290.0, 414.0, 414.0), "embedded"),
        ((286.0, 410.0, 410.0, 410.0, 410.0), "parsed"),
        ((370.0, 370.0, 370.0, 370.0, 370.0), "spooled"),  # peaked before the job started
        ((286.0, 290.0, 290.0, 300.0, 330.0), "job_end"),
    ],
)
def test_peak_phase_is_where_vmhwm_first_reaches_its_final_value(highs, expected):
    assert peak_phase(_phases(*highs)) == expected


def test_phase_table_handles_failed_and_restarted_runs():
    failed = _ready(1, status="failed", job={"phases": [{"phase": "spooled", "rss_mb": 280.0, "hwm_mb": 286.0},
                                                        {"phase": "job_end", "rss_mb": 281.0, "hwm_mb": 286.0}]})
    restarted = _ready(2, status="lost", restarted=True, job=None)
    table = phase_table([failed, restarted]).splitlines()
    assert table[2] == "| 1 | 280.0 / 286.0 | - | - | - | 281.0 / 286.0 | spooled |"
    assert table[3] == "| 2 | - | - | - | - | - | - |"
